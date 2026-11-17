"""Manage optional local cuDSS calibration components, separate from delivery."""
import datetime
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from . import runtime as core

BACKENDS = {'cudss': '原生 NVIDIA cuDSS', 'mycudss-direct': 'cuDSS 直接封装', 'mycudss-worker': 'cuDSS worker 封装','pangu':'PanguLU'}


def directory():
    return core.ROOT / 'runtime/tests/mycudss'


def native_library():
    path=Path(core.cfg()['comsol_root'])/'ext/cudss/glnxa64/libcudss.so.0.7.1'
    if not path.is_file():
        raise ValueError('COMSOL bundled cuDSS 0.7.1 not found')
    return path.resolve()


def sources(value=None):
    if value:
        return Path(value).expanduser().resolve(strict=True)
    local=core.ROOT/'dev/benchmarks/mycudss'
    if local.is_dir():
        return local
    manifest=directory()/'manifest.json'
    if manifest.is_file():
        return Path(core.read(manifest)['source_directory'])
    raise ValueError('Optional test sources are not installed; test build --source /path/to/mycudss')


def verify():
    root=directory();manifest=root/'manifest.json'
    if not manifest.is_file():
        raise ValueError('cuDSS test components are not built; run pangu-comsol test build')
    value=core.read(manifest)
    if value.get('version')!=1 or not isinstance(value.get('files'),dict):
        raise ValueError('Unsupported test component manifest; rebuild the optional tests')
    if value.get('native_library_sha256')!=core.sha(native_library()):
        raise ValueError('Native cuDSS changed; rebuild the optional test components')
    for name,digest in value['files'].items():
        path=root/name
        if not path.is_file() or core.sha(path)!=digest:
            raise ValueError('Test component changed or missing: '+name)
    return value


def status():
    try:
        native=str(native_library());native_error=''
    except (OSError,ValueError) as error:
        native='';native_error=str(error)
    try:
        verify();test_error=''
    except (OSError,ValueError,KeyError) as error:
        test_error=str(error)
    try:core.ensure_module();pangu_error=''
    except (OSError,ValueError,KeyError) as error:pangu_error=str(error)
    errors={'cudss':native_error,'pangu':pangu_error,'mycudss-direct':test_error,'mycudss-worker':test_error}
    return {'directory':str(directory()),'pangu_directory':str(core.module_dir()),'backends':[{'id':name,'label':label,'available':not errors[name],
             'message':errors[name]} for name,label in BACKENDS.items()], 'native_library':native}


def build(source=None):
    source=sources(source)
    names=['direct.cpp','bridge.cpp','worker.cpp','protocol.h','profile.h']
    for name in names:
        if not (source/name).is_file():raise ValueError('Missing optional test source: '+str(source/name))
    native=native_library();cuda=Path(core.cfg()['cuda_root']);target=directory();target.parent.mkdir(parents=True,exist_ok=True)
    flags=['g++','-O2','-std=c++17','-fPIC','-DPGL_PROFILE=0','-I'+str(core.production_sources()/'include/cudss'),'-I'+str(cuda/'include'),'-I'+str(source)]
    with tempfile.TemporaryDirectory(prefix='.mycudss-build-',dir=target.parent) as folder:
        stage=Path(folder)/'components'
        for mode in ['direct','worker']:(stage/mode).mkdir(parents=True)
        commands=[flags+['-shared',str(source/'direct.cpp'),'-ldl','-Wl,-soname,libcudss.so.0','-o',str(stage/'direct/libcudss.so.0.7.1')],
                  flags+['-shared',str(source/'bridge.cpp'),'-L'+str(cuda/'lib64'),'-lcudart','-Wl,-soname,libcudss.so.0','-o',str(stage/'worker/libcudss.so.0.7.1')],
                  flags+[str(source/'worker.cpp'),'-L'+str(native.parent),'-l:libcudss.so.0.7.1','-L'+str(cuda/'lib64'),
                         '-Wl,-rpath-link,'+str(cuda/'lib64'),'-lcudart','-o',str(stage/'worker/mycudss_worker')]]
        with (stage/'build.log').open('w') as log:
            try:
                for command in commands:
                    log.write(repr(command)+'\n');log.flush()
                    subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
            except subprocess.CalledProcessError as error:
                log.flush();failure=core.ROOT/'data/runs'/('test-build_'+datetime.datetime.now().strftime('%Y%m%dT%H%M%S_%f'))
                failure.mkdir(parents=True);shutil.copy2(stage/'build.log',failure/'build.log')
                raise RuntimeError('Test component build failed; see '+str(failure/'build.log')) from error
        for mode in ['direct','worker']:
            for name in ['libcudss.so','libcudss.so.0']:(stage/mode/name).symlink_to('libcudss.so.0.7.1')
        value={'version':1,'built_at':datetime.datetime.now().astimezone().isoformat(),'source_directory':str(source),
               'native_library':str(native),'native_library_sha256':core.sha(native),'cuda_root':str(cuda),
               'source_sha256':{name:core.sha(source/name) for name in names},
               'files':{str(p.relative_to(stage)):core.sha(p) for p in stage.rglob('*') if p.is_file() and not p.is_symlink() and p.name!='build.log'}}
        core.write(stage/'manifest.json',value)
        previous=Path(folder)/'previous'
        if target.exists():target.rename(previous)
        try:stage.rename(target)
        except BaseException:
            if previous.exists():previous.rename(target)
            raise
    return {'built':True,'directory':str(target),'native_library':str(native),'native_library_sha256':value['native_library_sha256']}


def environment(backend,threads=None):
    verify();native=native_library();cuda=Path(core.cfg()['cuda_root']);mode=backend.removeprefix('mycudss-')
    output=directory()/mode
    if mode not in {'direct','worker'}:raise ValueError('Unknown test backend: '+backend)
    env=core.base_env(threads);env['LD_LIBRARY_PATH']=str(cuda/'lib64');env['MYCUDSS_LIBRARY']=str(native)
    info={'backend':backend,'test_backend':True,'library':str(output/'libcudss.so.0.7.1'),
          'library_sha256':core.sha(output/'libcudss.so.0.7.1'),'native_library':str(native),'native_library_sha256':core.sha(native)}
    if mode=='worker':
        worker=output/'mycudss_worker';env['MYCUDSS_WORKER']=str(worker)
        env['MYCUDSS_WORKER_LD_LIBRARY_PATH']=str(native.parent)+os.pathsep+str(cuda/'lib64')
        info.update(worker=str(worker),worker_sha256=core.sha(worker))
    return output,env,info


def benchmark(args):
    if not 1<=args.threads<=len(os.sched_getaffinity(0)):raise ValueError('threads exceeds available CPU affinity')
    if not 1<=args.repeat<=10:raise ValueError('repeat must be 1..10')
    if args.execution is not None and args.backend!='pangu':raise ValueError('Execution mode applies only to PanguLU')
    script=sources()/'benchmark.py'
    if not script.is_file():raise ValueError('Optional S benchmark tools are not installed')
    command=[sys.executable,'-B',str(script),'--backend',args.backend,'--project',str(args.project.expanduser().resolve()),
             '--frequencies',args.frequencies,'--threads',str(args.threads),'--repeat',str(args.repeat)]
    if args.execution is not None:command+=['--execution',args.execution]
    return subprocess.call(command,env=dict(os.environ,PANGU_COMSOL_HOME=str(core.ROOT)))
