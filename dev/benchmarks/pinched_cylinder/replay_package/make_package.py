#!/usr/bin/env python3
"""Select the experiment files; omit machine installations and checkpoint caches."""
from pathlib import Path
import argparse
import datetime
import hashlib
import json
import os
import shutil
import tarfile

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
TRIAL = ROOT / 'data/runs/pinched-cylinder_20261116T184505'
WORK = ROOT / 'data/runs/pinched-cylinder-package_20261116T200105'
STAGE = WORK / 'stage/pinched-cylinder-replay'
EXPORT = ROOT / 'data/exports/pinched-cylinder_20261116T200105'


def sha(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def copy(source, relative, link=False):
    target = STAGE / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    if link:
        os.link(source, target)
    else:
        shutil.copy2(source, target)


def clean_walk(root):
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in ['__pycache__', '.git'])
        for name in sorted(files):
            if not name.endswith(('.pyc', '.pyo')):
                yield Path(current) / name


def stage():
    STAGE.mkdir(parents=True, exist_ok=True)
    copy(HERE / 'replay.py', 'replay.py')
    (STAGE / 'replay.py').chmod(0o755)
    copy(HERE / 'README-REPLAY.txt', 'README-REPLAY.txt')
    for name in ['duel.cpp', 'activity.cpp', 'activity.h', 'analyze.py']:
        source = TRIAL / 'harness' / name
        copy(source, 'src/' + name)
    source = ROOT / 'dev/benchmarks/mycudss/direct.cpp'
    mymanifest = json.loads((ROOT / 'runtime/tests/mycudss/manifest.json').read_text())
    assert sha(source) == mymanifest['source_sha256']['direct.cpp']
    copy(source, 'src/mycudss-direct.cpp')
    copy(ROOT / 'dev/benchmarks/pinched_cylinder/original_host.cpp', 'src/original-host.cpp')
    # Metadata only: preserve all numerical/timing code and add physical CUDA UUID.
    target = STAGE / 'src/duel.cpp'
    text = target.read_text()
    old = next(line for line in text.splitlines() if line.startswith(' std::cout<<"DEVICE '))
    new = ''' static const char hex_digits[]="0123456789abcdef";std::string device_uuid;
 for(unsigned char byte:prop.uuid.bytes){device_uuid+=hex_digits[byte>>4];device_uuid+=hex_digits[byte&15];}
 std::cout<<"DEVICE {\\"ordinal\\":"<<ordinal<<",\\"name\\":\\""<<prop.name<<"\\",\\"uuid_hex\\":\\""<<device_uuid<<"\\",\\"pid\\":"<<getpid()<<"}"<<std::endl;'''
    target.write_text(text.replace(old, new))
    for source in clean_walk(TRIAL / 'sources/MOOSE-with-Solved-Tech'):
        copy(source, 'upstream/MOOSE-with-Solved-Tech/' + str(source.relative_to(TRIAL / 'sources/MOOSE-with-Solved-Tech')))
    for directory in ['harness', 'pilot-harness', 'eight-rhs-harness']:
        for source in clean_walk(TRIAL / directory):
            copy(source, 'provenance/measured-harnesses/' + directory + '/' + str(source.relative_to(TRIAL / directory)))
    for source in (ROOT / 'dev/benchmarks/pinched_cylinder').iterdir():
        if source.is_file():
            copy(source, 'provenance/original-scripts/pinched_cylinder/' + source.name)
    for source in (ROOT / 'dev/benchmarks/matrix_duel').iterdir():
        if source.is_file():
            copy(source, 'provenance/original-scripts/matrix_duel/' + source.name)
    copy(ROOT / 'dev/benchmarks/mycudss/source-provenance.json', 'provenance/mycudss-source-provenance.json')
    copy(ROOT / 'runtime/tests/mycudss/manifest.json', 'provenance/measured-mycudss-manifest.json')
    write(STAGE / 'provenance/portable-changes.json', {
        'original_experiment': str(TRIAL),
        'case_repository': 'https://github.com/solved-tech-dev/case-studies/tree/main/MOOSE-with-Solved-Tech',
        'case_commit': 'acce983316ed368f7dd260d36b2a2329907ea208',
        'base_measured_duel_sha256': sha(TRIAL / 'harness/duel.cpp'),
        'portable_duel_sha256': sha(STAGE / 'src/duel.cpp'),
        'changes': ['Replace module-specific Python imports and fixed paths with standalone configure/build/run.',
                    'C++ change only adds CUDA UUID metadata before phase timing; matrix math, APIs and phase clocks retained.',
                    'Vendor headers/libraries and keys supplied by the destination, never copied into these archives.',
                    'Add direct NVIDIA cuDSS option alongside the same calibrated forwarding shim.',
                    'Keep failed traces as evidence; tracing is opt-in and bounded.']})
    (STAGE / 'NOTICE.txt').write_text('This is a private experimental replay bundle, separate from the production module.\n'
        'Original public case sources and copyright/license notices are preserved under upstream/MOOSE-with-Solved-Tech/.\n'
        'NVIDIA CUDA/cuDSS SDK files, the PanguLU SDK and device license keys must be provided by the destination installation.\n')
    code_prefixes = ['src', 'upstream', 'provenance']
    code_files = [STAGE / x for x in ['replay.py', 'README-REPLAY.txt', 'NOTICE.txt']]
    for prefix in code_prefixes:
        code_files.extend(clean_walk(STAGE / prefix))
    write(STAGE / 'manifests/code-files.json', {'sha256': {str(p.relative_to(STAGE)): sha(p) for p in sorted(code_files)}})
    for source in clean_walk(TRIAL / 'datasets'):
        copy(source, 'data/matrices/' + str(source.relative_to(TRIAL / 'datasets')), True)
    skip = {'conda', 'moose-env', 'bin', 'pilot-bin', 'eight-rhs-bin', 'datasets', 'sources', 'harness', 'pilot-harness', 'eight-rhs-harness', 'physics_cp'}
    duplicate = 'million-pangulu-app/A.petsc'
    a = TRIAL / 'million-reference-app/A.petsc'
    assert sha(a) == sha(TRIAL / duplicate)
    omitted = []
    for current, dirs, files in os.walk(TRIAL):
        dirs[:] = sorted(x for x in dirs if x not in skip and x != '__pycache__')
        for name in sorted(files):
            source = Path(current) / name
            relative = str(source.relative_to(TRIAL))
            if relative == duplicate:
                omitted.append({'path': relative, 'equivalent_in_archive': 'evidence/original-run/million-reference-app/A.petsc', 'sha256': sha(a)})
                continue
            if name.endswith(('.so', '.o', '.pyc', '.pyo')) or name.startswith('libmoose_pangulu.so'):
                continue
            if relative.startswith('tools/') and name not in ['micromamba-source.json', 'inl-linux-64-repodata.json', 'inl-noarch-repodata.json']:
                continue
            copy(source, 'evidence/original-run/' + relative, True)
    write(STAGE / 'manifests/data-selection.json', {
        'source': str(TRIAL), 'included': ['canonical small and million CSC matrices, original RHS and MUMPS references',
            'one raw million PETSc matrix plus capture vectors', 'full final physical fields/CSV and model meshes',
            'success/failure logs, GPU traces, reports, hashes and environment lock metadata'],
        'excluded': ['Conda package cache and installed MOOSE environment', 'physics_cp checkpoint caches',
            'machine-built executables, objects and shared libraries', 'PanguLU/CUDA/cuDSS SDK binaries/headers and all license keys'],
        'deduplicated_identical_matrix': omitted})
    validation = WORK / 'acceptance/relocated workspace/pinched-cylinder-replay'
    if validation.is_dir():
        for source in clean_walk(validation / 'runs'):
            copy(source, 'evidence/portability-validation/relocated-runs/' + str(source.relative_to(validation / 'runs')))
        for source in clean_walk(WORK / 'original-binary-control'):
            copy(source, 'evidence/portability-validation/original-binary-control/' + source.name)
        status = EXPORT / 'portability-check.json'
        if status.is_file():
            value = json.loads(status.read_text())
            value.pop('archives', None)
            value['note'] = 'Preliminary relocated validation; numerical C++ source is unchanged in the final bundle. Final archive checksums and final default-path acceptance are beside the archives.'
            write(STAGE / 'evidence/portability-validation/preliminary-check.json', value)
    data_files = []
    for prefix in ['data', 'evidence']:
        data_files.extend(clean_walk(STAGE / prefix))
    data_files.append(STAGE / 'manifests/data-selection.json')
    write(STAGE / 'manifests/data-files.json', {'sha256': {str(p.relative_to(STAGE)): sha(p) for p in sorted(data_files)}})
    summary = {'stage': str(STAGE), 'code_files': len(code_files), 'data_files': len(data_files),
               'code_bytes': sum(p.stat().st_size for p in code_files), 'data_bytes': sum(p.stat().st_size for p in data_files)}
    write(WORK / 'staging.json', summary)
    print(json.dumps(summary, indent=2), flush=True)


def archive():
    EXPORT.mkdir(parents=True, exist_ok=True)
    results = []
    for kind in ['code', 'data']:
        manifest = STAGE / 'manifests' / (kind + '-files.json')
        paths = sorted(json.loads(manifest.read_text())['sha256']) + [str(manifest.relative_to(STAGE))]
        target = EXPORT / ('pinched-cylinder-' + kind + '.tar.gz')
        print('ARCHIVING', kind, len(paths), flush=True)
        with tarfile.open(target, 'w:gz', compresslevel=6, dereference=True) as output:
            for relative in paths:
                source = STAGE / relative
                info = output.gettarinfo(str(source), arcname='pinched-cylinder-replay/' + relative)
                info.uid = info.gid = 0
                info.uname = info.gname = ''
                info.mode = 0o755 if relative == 'replay.py' else 0o644
                with source.open('rb') as stream:
                    output.addfile(info, stream)
        result = {'archive': target.name, 'bytes': target.stat().st_size, 'sha256': sha(target), 'files': len(paths)}
        results.append(result)
        print(json.dumps(result), flush=True)
    (EXPORT / 'SHA256SUMS').write_text(''.join(x['sha256'] + '  ' + x['archive'] + '\n' for x in results))
    write(EXPORT / 'packaging-report.json', {'created_at': datetime.datetime.now().astimezone().isoformat(), 'archives': results, 'staging': json.loads((WORK / 'staging.json').read_text())})
    print('EXPORTED', EXPORT, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['stage', 'archive'])
    args = parser.parse_args()
    stage() if args.action == 'stage' else archive()
