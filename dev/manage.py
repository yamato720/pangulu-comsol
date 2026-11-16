#!/usr/bin/env python3
"""Developer-only native and COMSOL integration verification."""
import argparse, pathlib, subprocess, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0,str(ROOT/"src/python"))
from pangulu_comsol import runtime as pc


def compile_tests(out):
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    c = pc.cfg(); p = pc.sdk(); module = pc.module_dir()
    cuda = pathlib.Path(c['cuda_root']); comsol = pathlib.Path(c['comsol_root'])
    with (out / 'build.log').open('w') as log:
        subprocess.run(['g++', '-O2', '-std=c++17', '-I'+str(p/'include'), '-I'+str(cuda/'include'),
                        '-I'+str(ROOT/'src'), '-I'+str(ROOT/'src/include/cudss'), str(ROOT/'dev/tests/adapter_test.cpp'),
                        '-o', str(out/'adapter_test'), '-L'+str(module), '-L'+str(cuda/'lib64'), '-lcudss', '-lcudart'],
                       stdout=log, stderr=subprocess.STDOUT, check=True)
        (out/'java').mkdir(exist_ok=True)
        subprocess.run([str(comsol/'java/glnxa64/jre/bin/javac'), '-proc:none', '-classpath',
                        str(comsol/'plugins/*')+':'+str(module/'java'), '-d', str(out/'java'),
                        *map(str, sorted((ROOT/'dev/tests/java').glob('*.java')))], stdout=log, stderr=subprocess.STDOUT, check=True)
    return out


def run(out, integration=False):
    out = compile_tests(out)
    env = pc.clean_env(); env['LD_LIBRARY_PATH'] = str(pc.module_dir())+':'+env['LD_LIBRARY_PATH']
    with (out/'adapter.log').open('w') as log:
        subprocess.run([str(out/'adapter_test')], env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    assert 'ADAPTER_TEST_PASS' in (out/'adapter.log').read_text()
    if integration:
        command, env, module = pc.comsol_command(out/'comsol')
        command += ['batch', '-classpathadd', str(out/'java')+':'+str(module/'java'), '-nosave',
                    '-batchlog', str(out/'batch.log'), '-inputfile', str(out/'java/ModuleIntegrationTest.class'), str(out)]
        with (out/'stdout.log').open('w') as stdout, (out/'stderr.log').open('w') as stderr:
            result = subprocess.run(command, env=env, stdout=stdout, stderr=stderr)
        if result.returncode or 'MODULE_INTEGRATION_PASS' not in (out/'stdout.log').read_text() or 'worker_started' not in (out/'stderr.log').read_text():
            raise RuntimeError('COMSOL integration verification failed: '+str(out))
    pc.write(out/'result.json', {'status':'completed', 'adapter':True, 'COMSOL_integration':integration})
    print(out)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['adapter','integration'])
    parser.add_argument('--out', type=pathlib.Path, required=True)
    args=parser.parse_args();run(args.out.resolve(), args.action=='integration')
