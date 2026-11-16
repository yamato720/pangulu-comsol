#!/usr/bin/env python3
"""Assemble the customer payload from an explicit allowlist, without local keys."""
import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0,str(ROOT/"src/python"))
from pangulu_comsol import runtime as pc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=pathlib.Path, default=ROOT / 'data/releases')
    parser.add_argument('--licence', type=pathlib.Path, action='append', default=[],
                        help='Explicit customer key to include; repeat for multiple keys. No local keys are copied by default.')
    args = parser.parse_args()
    destination = args.output.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / 'pangulu-comsol'
    archive = destination / 'pangulu-comsol-0.3.0-linux-x86_64.tar.gz'
    if target.exists() or archive.exists():
        raise ValueError('Choose a fresh --output directory; existing delivery is preserved')
    pc.verify(pc.sdk())
    pc.ensure_module()
    with tempfile.TemporaryDirectory(prefix='.assemble-', dir=destination) as temp:
        stage = pathlib.Path(temp) / 'pangulu-comsol'
        stage.mkdir()
        for name in ['pangu-comsol', 'start.sh']:
            shutil.copy2(ROOT / name, stage / name)
        for name in ['config', 'licence', 'runtime/module', 'runtime/cuda/lib64', 'runtime/cuda/licenses', 'pangu']:
            (stage / name).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / 'config/distribution.json', stage / 'config/runtime.json')
        shutil.copyfile(ROOT / 'licence/README.txt', stage / 'licence/README.txt')
        for source in args.licence:
            source = source.resolve(strict=True)
            if source.suffix not in {'.key', '.license'} or ',' in source.name:
                raise ValueError('Expected an original .key or .license file: ' + str(source))
            target_key = stage / 'licence' / source.name
            if target_key.exists():
                raise ValueError('Duplicate customer licence filename: ' + source.name)
            shutil.copyfile(source, target_key)
            target_key.chmod(0o600)
        shutil.copyfile(ROOT / 'docs/customer-README.txt', stage / 'README.txt')
        shutil.copytree(ROOT / 'src', stage / 'src', ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.class'))
        sdk = pc.sdk()
        shutil.copytree(sdk, stage / 'pangu' / sdk.name)
        module = pc.module_dir()
        for name in ['native_license', 'pangulu_worker', 'libpangu_backend.so', 'libcudss.so.0.7.1', 'libcudss.so.0', 'libcudss.so',
                     'PanguModule.jar', 'PanguDesktop.jar', 'manifest.json']:
            shutil.copy2(module / name, stage / 'runtime/module' / name, follow_symlinks=False)
        shutil.copytree(module / 'java', stage / 'runtime/module/java')
        for jar_name in ['PanguModule.jar', 'PanguDesktop.jar']:
            with zipfile.ZipFile(stage / 'runtime/module' / jar_name) as jar:
                if any('Test' in name for name in jar.namelist()):
                    raise ValueError('Test class in production JAR')
        cuda = pathlib.Path(pc.cfg()['cuda_root'])
        for name in ['libcudart', 'libcublas', 'libcublasLt']:
            original = (cuda / 'lib64' / (name + '.so.12')).resolve(strict=True)
            shutil.copy2(original, stage / 'runtime/cuda/lib64' / (name + '.so.12'))
            (stage / 'runtime/cuda/lib64' / (name + '.so')).symlink_to(name + '.so.12')
        dependency_root = ROOT / 'runtime/dependencies'
        for package in ['nvidia_cuda_runtime_cu12-12.9.79', 'nvidia_cublas_cu12-12.9.2.10']:
            shutil.copyfile(dependency_root / 'packages' / (package + '.dist-info/licenses/License.txt'),
                            stage / 'runtime/cuda/licenses' / (package + '.txt'))
        pc.write(stage / 'runtime/cuda/provenance.json', pc.read(dependency_root / 'manifest.json'))
        versions = {'GLIBC': set(), 'GLIBCXX': set()}
        for name in ['native_license', 'pangulu_worker', 'libpangu_backend.so', 'libcudss.so.0.7.1']:
            data = subprocess.check_output(['readelf', '--version-info', str(module / name)], text=True)
            for family in versions:
                versions[family].update(re.findall(r'\b' + family + r'_(\d+(?:\.\d+)+)', data))
            dynamic = subprocess.check_output(['readelf', '-d', str(module / name)], text=True)
            if '/home/' in dynamic or '/mnt/' in dynamic:
                raise ValueError('Absolute developer path in production native library')
        requirements = {k: max(v, key=lambda x: tuple(map(int, x.split('.')))) for k, v in versions.items()}
        pc.write(stage / 'release.json', dict(module_version=pc.read(module/'manifest.json')['module_version'], pangu=pc.sdk_info(), system_library_requirements=requirements,
                 cuda_runtime='12.9.79', cublas='12.9.2.10', comsol_series='6.4', platform='Linux x86_64',
                 customer_licence_included=bool(args.licence), internal_tests_included=False))
        info = pc.read(stage / 'release.json')
        info['pangu']['path'] = 'pangu/' + sdk.name
        pc.write(stage / 'release.json', info)
        files = sorted(p for p in stage.rglob('*') if p.is_file() and not p.is_symlink())
        (stage / 'SHA256SUMS').write_text(''.join(pc.sha(p) + '  ' + str(p.relative_to(stage)) + '\n' for p in files))
        if not args.licence and (list((stage / 'licence').glob('*.key')) or list((stage / 'licence').glob('*.license'))):
            raise ValueError('A local customer licence entered the generic payload')
        stage.rename(target)
    with tarfile.open(archive, 'w:gz', compresslevel=1) as tar:
        tar.add(target, arcname='pangulu-comsol')
    if args.licence:
        archive.chmod(0o600)
    pc.write(destination / 'package.json', dict(archive=archive.name, sha256=pc.sha(archive), bytes=archive.stat().st_size,
             unpacked_directory=target.name, licence_included=bool(args.licence), system_library_requirements=requirements))
    (destination / 'SHA256SUMS').write_text(pc.sha(archive) + '  ' + archive.name + '\n')
    print(archive)
    print('Unpacked prototype:', target)


if __name__ == '__main__':
    main()
