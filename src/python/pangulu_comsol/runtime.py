#!/usr/bin/env python3
"""COMSOL launcher with a replaceable vendor SDK and central licence folder."""
import datetime, hashlib, json, os, pathlib, re, shutil, subprocess, tarfile, tempfile, zipfile

ROOT = pathlib.Path(__file__).resolve().parents[3]
MODULE_ABI = 2


def read(path):
    return json.loads(pathlib.Path(path).read_text())


def write(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def sha(path):
    with pathlib.Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def discover_comsol():
    candidates = []
    if os.environ.get('COMSOL_ROOT'):
        candidates.append(pathlib.Path(os.environ['COMSOL_ROOT']).expanduser())
    if executable := shutil.which('comsol'):
        candidates.append(pathlib.Path(executable).resolve().parent.parent)
    for base in [pathlib.Path.home(), pathlib.Path('/opt'), pathlib.Path('/usr/local')]:
        for pattern in ['[Cc][Oo][Mm][Ss][Oo][Ll]*/multiphysics', '[Cc][Oo][Mm][Ss][Oo][Ll]*/*/multiphysics']:
            candidates.extend(base.glob(pattern))
    return sorted({str(p.resolve()) for p in candidates if (p / 'bin/comsol').is_file() and (p / 'plugins').is_dir()})


def settings():
    c = dict(licensed_gpu_index=0, ordering=0, gui_global_options=True,
             comsol_root='', cuda_root='runtime/cuda', licence_files=[])
    template = ROOT / 'config/distribution.json'
    if template.is_file():
        c.update(read(template))
    local = ROOT / 'config/runtime.json'
    if local.is_file():
        c.update(read(local))
    return c


def cfg():
    c = settings()
    if not c['comsol_root']:
        found = discover_comsol()
        if len(found) != 1:
            raise ValueError('Run pangu-comsol init --comsol <installation>. Discovered installations: ' + str(found))
        c['comsol_root'] = found[0]
    for key in ['comsol_root', 'cuda_root']:
        path = pathlib.Path(c[key]).expanduser()
        c[key] = str(path if path.is_absolute() else ROOT / path)
    if not (pathlib.Path(c['comsol_root']) / 'bin/comsol').is_file():
        raise ValueError('COMSOL executable not found under comsol_root')
    return c


def sdk():
    container = ROOT / 'pangu'
    choices = [p.parent for p in container.glob('*/include')
               if (p / 'pangulu.h').is_file() and (p.parent / 'lib/libpangulu.so').is_file()]
    if len(choices) != 1:
        raise ValueError('Keep exactly one complete extracted Pangu release inside pangu/. Found: ' + str(choices))
    path = choices[0].resolve()
    if not path.is_relative_to(container.resolve()):
        raise ValueError('Pangu SDK must be inside this module\'s pangu/ folder')
    return path


def sdk_info(path=None):
    path = pathlib.Path(path or sdk())
    header = (path / 'include/pangulu.h').read_text()
    version = re.search(r'PANGULU_VERSION_STRING\s+"([A-Za-z0-9._+-]+)"', header)
    abi = re.search(r'PANGULU_ABI_VERSION\s+(\d+)', header)
    if not version or not abi:
        raise ValueError('Pangu release has no recognized version or ABI')
    if int(abi[1]) != MODULE_ABI:
        raise ValueError('This module supports Pangu ABI 2; install a matching module for ABI ' + abi[1])
    return dict(version=version[1], abi=int(abi[1]), path=str(path), core_sha256=sha(path / 'lib/libpangulu.so'))


def verify(path):
    path = pathlib.Path(path).resolve()
    checked = set()
    for line in (path / 'SHA256SUMS').read_text().splitlines():
        if not line.strip():
            continue
        digest, name = line.split(maxsplit=1)
        name = name.lstrip('*')
        file = path / name
        if not re.fullmatch('[0-9a-fA-F]{64}', digest) or not file.resolve().is_relative_to(path):
            raise ValueError('Invalid SDK checksum entry: ' + name)
        if sha(file) != digest.lower():
            raise ValueError('SDK checksum mismatch: ' + name)
        checked.add(pathlib.Path(name).as_posix())
    if not {'include/pangulu.h', 'lib/libpangulu.so'} <= checked:
        raise ValueError('SDK checksums must cover the public header and core library')
    sdk_info(path)
    return True


def install(archive):
    """Validate first, replace the complete SDK, preserve licence/."""
    archive = pathlib.Path(archive).resolve(strict=True)
    runtime = ROOT / 'runtime'
    runtime.mkdir(exist_ok=True)
    for proc in pathlib.Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            if ('PC_APP_ROOT=' + str(ROOT)).encode() in (proc / 'environ').read_bytes().split(b'\0'):
                raise ValueError('Close running COMSOL/module processes before updating Pangu')
        except (PermissionError, FileNotFoundError, ProcessLookupError):
            pass
    with tempfile.TemporaryDirectory(prefix='.sdk-update-', dir=runtime) as temp:
        container = pathlib.Path(temp) / 'pangu'
        container.mkdir()
        with tarfile.open(archive) as tar:
            tar.extractall(container, filter='data')
        roots = [p.parent.parent for p in container.glob('*/include/pangulu.h') if (p.parent.parent / 'lib/libpangulu.so').is_file()]
        if len(roots) != 1 or len(list(container.iterdir())) != 1:
            raise ValueError('Archive must contain one complete Pangu release directory')
        verify(roots[0])
        info = sdk_info(roots[0])
        target, previous = ROOT / 'pangu', pathlib.Path(temp) / 'previous-pangu'
        info.update(path=str(target / roots[0].name), archive_name=archive.name, archive_sha256=sha(archive))
        if target.exists():
            target.rename(previous)
        try:
            container.rename(target)
            write(runtime / 'sdk-install.json', info)
        except BaseException:
            if target.exists():
                target.rename(container)
            if previous.exists():
                previous.rename(target)
            raise
    print('Pangu SDK replaced:', info['version'], '; licence/ preserved')
    return info


def licence_paths():
    folder = ROOT / 'licence'
    names = settings()['licence_files']
    paths = [folder / name for name in names] if names else sorted(folder.glob('*.key')) + sorted(folder.glob('*.license'))
    if not paths:
        raise ValueError('Place your original signed .key file in licence/ and start again')
    for path in paths:
        if not path.is_file() or not path.resolve().is_relative_to(folder.resolve()) or ',' in str(path):
            raise ValueError('Invalid licence file in licence/: ' + str(path))
    return paths


def license_add(source, replace=False):
    source = pathlib.Path(source).resolve(strict=True)
    folder = ROOT / 'licence'
    folder.mkdir(exist_ok=True)
    target = folder / source.name
    if target.exists() and sha(target) != sha(source) and not replace:
        raise ValueError('A different licence with this filename exists; use license add --replace to update it')
    if source != target.resolve():
        with tempfile.NamedTemporaryFile(prefix='.licence-', dir=folder, delete=False) as stream:
            temporary = pathlib.Path(stream.name)
        try:
            shutil.copyfile(source, temporary)
            temporary.chmod(0o600)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    target.chmod(0o600)
    print('Original licence copied to:', target)


def module_dir():
    return ROOT / 'runtime/module'


def base_env(threads=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith(('PGL_', 'PC_', 'PANGULU_', 'LD_'))}
    if threads is not None:
        if threads < 1:
            raise ValueError('--threads must be a positive integer')
        env.update(OMP_NUM_THREADS=str(threads), MKL_NUM_THREADS=str(threads), OPENBLAS_NUM_THREADS=str(threads))
    return env


def clean_env(p=None, threads=None):
    c = cfg()
    p = p or sdk()
    env = base_env(threads)
    libraries = str(p / 'lib') + ':' + str(pathlib.Path(c['cuda_root']) / 'lib64')
    env.update(PANGULU_LICENSE_PATHS=','.join(map(str, licence_paths())), PC_LICENSED_GPU_INDEX=str(c['licensed_gpu_index']),
               PC_WORKER=str(module_dir() / 'pangulu_worker'), PC_WORKER_LIBRARY_PATH=libraries, LD_LIBRARY_PATH=libraries,
               PC_REORDERING=str(c['ordering']), PC_REUSE='refactor', PC_COMSOL_DIAGNOSTICS='1', PC_SESSION='1', PC_APP_ROOT=str(ROOT))
    return env


def java_sources():
    return sorted((ROOT / 'src/java').glob('*.java'))


def build_java(output):
    comsol = pathlib.Path(cfg()['comsol_root'])
    output = pathlib.Path(output)
    classes = output / 'java'
    classes.mkdir(parents=True)
    with (output / 'java.log').open('w') as log:
        subprocess.run([str(comsol / 'java/glnxa64/jre/bin/javac'), '-proc:none', '-classpath', str(comsol / 'plugins/*'),
                        '-d', str(classes), *map(str, java_sources())], stdout=log, stderr=subprocess.STDOUT, check=True)
    with zipfile.ZipFile(output / 'PanguModule.jar', 'w', zipfile.ZIP_DEFLATED) as jar:
        for file in (classes / 'org/pangulu/comsol').glob('PanguModule*.class'):
            jar.write(file, str(file.relative_to(classes)))
    manifest = ('Manifest-Version: 1.0\nBundle-ManifestVersion: 2\nBundle-SymbolicName: org.pangulu.comsol.desktop\n'
                'Bundle-Version: 0.3.0\nBundle-Activator: org.pangulu.comsol.PanguDesktop\nImport-Package: org.osgi.framework\n'
                'Require-Bundle: com.comsol.api,com.comsol.model,com.comsol.gui,com.comsol.guimph,org.eclipse.swt\n\n')
    lines = []
    for line in manifest.splitlines():
        while len(line) > 70:
            lines.append(line[:70])
            line = ' ' + line[70:]
        lines.append(line)
    with zipfile.ZipFile(output / 'PanguDesktop.jar', 'w', zipfile.ZIP_DEFLATED) as jar:
        jar.writestr('META-INF/MANIFEST.MF', '\r\n'.join(lines) + '\r\n')
        for file in (classes / 'org/pangulu/comsol').glob('*.class'):
            jar.write(file, str(file.relative_to(classes)))


def build():
    p = sdk()
    verify(p)
    c = cfg()
    cuda = pathlib.Path(c['cuda_root'])
    runtime = ROOT / 'runtime'
    runtime.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.module-build-', dir=runtime) as temp:
        output = pathlib.Path(temp) / 'module'
        output.mkdir()
        includes = ['-I' + str(p / 'include'), '-I' + str(cuda / 'include'), '-I' + str(ROOT / 'src')]
        libraries = ['-L' + str(p / 'lib'), '-L' + str(cuda / 'lib64'), '-Wl,--enable-new-dtags', '-Wl,-rpath,$ORIGIN/../cuda/lib64']
        native_libs = [*libraries, '-Wl,--no-as-needed', '-lpangulu', '-lcudart', '-lcublas', '-lcublasLt', '-ldl', '-lm']
        commands = [
            ['gcc', '-O2', '-std=gnu11', *includes, str(ROOT / 'src/native_license.c'), '-o', str(output / 'native_license'), *native_libs],
            ['gcc', '-O2', '-std=gnu11', '-DPGL_PROFILE=0', *includes, str(ROOT / 'src/worker.c'), '-o', str(output / 'pangulu_worker'), *native_libs, '-pthread'],
            ['g++', '-O2', '-std=c++17', '-shared', '-fPIC', '-DPGL_PROFILE=0', *includes, '-I' + str(ROOT / 'src/include/cudss'),
             str(ROOT / 'src/bridge.cpp'), '-o', str(output / 'libcudss.so.0.7.1'), *libraries, '-lcudart', '-Wl,-soname,libcudss.so.0']]
        with (output / 'build.log').open('w') as log:
            for command in commands:
                subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
        for name in ['libcudss.so', 'libcudss.so.0']:
            (output / name).symlink_to('libcudss.so.0.7.1')
        build_java(output)
        shipped = ['native_license', 'pangulu_worker', 'libcudss.so.0.7.1', 'PanguModule.jar', 'PanguDesktop.jar']
        sources = [*java_sources(), *(ROOT / 'src').glob('*.c'), *(ROOT / 'src').glob('*.cpp'), *(ROOT / 'src').glob('*.h')]
        write(output / 'manifest.json', dict(module_version='0.3.0', pangu_abi=MODULE_ABI, comsol_series='6.4', cudss_abi='0.7.1',
              files={name: sha(output / name) for name in shipped}, build_sdk_version=sdk_info(p)['version'],
              source_sha256={str(f.relative_to(ROOT)): sha(f) for f in sources}))
        target, previous = module_dir(), pathlib.Path(temp) / 'previous-module'
        if target.exists():
            target.rename(previous)
        try:
            output.rename(target)
        except BaseException:
            if previous.exists():
                previous.rename(target)
            raise
    print('Built production components:', module_dir())
    return module_dir()


def ensure_module():
    output = module_dir()
    manifest = read(output / 'manifest.json')
    if manifest['pangu_abi'] != sdk_info()['abi']:
        raise ValueError('Pangu ABI does not match this module; install an updated COMSOL module')
    for name, digest in manifest['files'].items():
        if sha(output / name) != digest:
            raise ValueError('Module file changed: ' + name + '; restore the distributed module')
    return output


def comsol_command(session, threads=None, backend='pangu'):
    c = cfg()
    session = pathlib.Path(session)
    session.mkdir(parents=True, exist_ok=True)
    if backend == 'pangu':
        p = sdk()
        verify(p)
        output = ensure_module()
        env = clean_env(p, threads)
        original_keys = licence_paths()
        keys = session / 'licence'
        keys.mkdir()
        for key in original_keys:
            shutil.copyfile(key, keys / key.name)
            (keys / key.name).chmod(0o600)
        env['PANGULU_LICENSE_PATHS'] = ','.join(str(keys / key.name) for key in original_keys)
        check = subprocess.run([str(output / 'native_license')], env=env, text=True, capture_output=True)
        (session / 'native-license.log').write_text(check.stdout + check.stderr)
        if check.returncode or 'NATIVE_LICENSE_OK' not in check.stdout:
            raise RuntimeError('Pangu/GPU/licence preflight failed; see ' + str(session / 'native-license.log'))
        info = dict(backend=backend, sdk=sdk_info(p), worker=str(output / 'pangulu_worker'), library=str(output / 'libcudss.so.0.7.1'),
                    library_sha256=sha(output / 'libcudss.so.0.7.1'), licences=[dict(name=key.name, sha256=sha(key)) for key in original_keys])
    elif backend == 'cudss':
        output = pathlib.Path(c['comsol_root']) / 'ext/cudss/glnxa64'
        library = output / 'libcudss.so.0.7.1'
        if not library.is_file():
            raise ValueError('COMSOL bundled cuDSS 0.7.1 not found')
        env = base_env(threads)
        env['LD_LIBRARY_PATH'] = str(pathlib.Path(c['cuda_root']) / 'lib64')
        info = dict(backend=backend, library=str(library), library_sha256=sha(library))
    else:
        raise ValueError('Unknown backend: ' + backend)
    command = [str(pathlib.Path(c['comsol_root']) / 'bin/comsol'), '-cudaroot', c['cuda_root'], '-cudssroot', str(output)]
    if threads is not None:
        command += ['-np', str(threads)]
    write(session / 'session.json', dict(info, command=command, comsol_preferences='inherit', explicit_threads=threads))
    return command, env, module_dir()


def doctor(output=None):
    p = sdk()
    verify(p)
    ensure_module()
    result = subprocess.run([str(module_dir() / 'native_license')], env=clean_env(), text=True, capture_output=True)
    report = dict(status='completed' if result.returncode == 0 and 'NATIVE_LICENSE_OK' in result.stdout else 'failed', sdk=sdk_info(p),
                  comsol_root=cfg()['comsol_root'], licence_files=[p.name for p in licence_paths()], exit_code=result.returncode,
                  stdout=result.stdout, stderr=result.stderr)
    if output:
        write(output, report)
    print(result.stdout, end='')
    if report['status'] != 'completed':
        raise RuntimeError('Pangu/GPU/licence check failed: ' + result.stderr.strip())
    return report


def desktop_bundles():
    """Extend the installed desktop's bundle list only for this process."""
    ini = pathlib.Path(cfg()['comsol_root']) / 'bin/glnxa64/comsol.ini'
    entries = [line.strip().split('=', 1)[1] for line in ini.read_text().splitlines()
               if line.strip().startswith('-Dosgi.bundles=')]
    if not entries:
        raise ValueError('No desktop bundle list found in COMSOL launcher ini')
    return entries[-1]


def launch(args):
    source = pathlib.Path(args.input).resolve(strict=True) if args.input else None
    backend = getattr(args, 'backend', 'pangu')
    session = ROOT / 'data/runs' / ('comsol_' + datetime.datetime.now().strftime('%Y%m%dT%H%M%S_%f'))
    command, env, output = comsol_command(session, args.threads, backend)
    if args.cmd == 'run':
        info = read(session / 'session.json')
        global_options = backend == 'pangu' and cfg()['gui_global_options']
        if global_options:
            bundles = desktop_bundles() + ',reference:' + (output / 'PanguDesktop.jar').as_uri() + '@5:start'
            command += ['-Dosgi.bundles=' + bundles, '-Dpangu.session.uri=' + session.as_uri()]
        if source:
            (session / 'models').mkdir()
            working = session / 'models' / (source.stem + '-work' + source.suffix)
            digest = sha(source)
            shutil.copy2(source, working)
            if sha(working) != digest or sha(source) != digest:
                raise RuntimeError('Input changed while creating a working copy')
            info.update(model_source=str(source), source_sha256=digest, working_model=str(working))
            command += ['-open', str(working)]
            print('Working model:', working, flush=True)
        info.update(global_options=global_options, command=command)
        write(session / 'session.json', info)
        print('Session:', session, flush=True)
        return subprocess.call(command, env=env)
    destination = pathlib.Path(args.output).resolve() if args.output else session / 'configured.mph'
    if destination == source or destination.exists():
        raise ValueError('Choose a new output path; source overwrite is refused')
    command += ['batch', '-classpathadd', str(output / 'java'), '-nosave', '-batchlog', str(session / 'batch.log'),
                '-inputfile', str(output / 'java/PanguBatch.class'), args.cmd, str(source), str(destination), args.target, 'run' if args.run else 'configure']
    with (session / 'stdout.log').open('w') as stdout, (session / 'stderr.log').open('w') as stderr:
        result = subprocess.run(command, env=env, stdout=stdout, stderr=stderr)
    text = (session / 'stdout.log').read_text()
    print(text)
    print('Session:', session)
    marker = 'PANGU_MODEL_INSPECT_END' if args.cmd == 'inspect' else 'PANGU_CONFIG_COMPLETE'
    if result.returncode or marker not in text:
        raise RuntimeError('COMSOL command failed; inspect session logs')
    if args.run and ('PANGU_RUN_COMPLETE' not in text or (args.cmd == 'configure' and 'worker_started' not in (session / 'stderr.log').read_text())):
        raise RuntimeError('Requested solve/backend was not verified')
