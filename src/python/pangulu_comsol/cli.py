"""PanguLU–COMSOL CLI for configuration, execution, local SDK packages and licences."""
import argparse
import json
import pathlib
import subprocess
import sys
import tempfile
from . import runtime as core
from . import development
from . import __version__

VERSION = __version__
CONFIG_KEYS = {'comsol_root', 'cuda_root', 'licensed_gpu_index', 'ordering', 'gui_global_options', 'licence_files'}


def display(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def config_file():
    return core.ROOT / 'config/runtime.json'


def save_settings(values):
    path = config_file()
    if not path.exists() or core.read(path) != values:
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', prefix='.config-', suffix='.tmp',
                                             dir=path.parent, delete=False) as stream:
                temporary = pathlib.Path(stream.name)
                json.dump(values, stream, ensure_ascii=False, indent=2)
                stream.write('\n')
            temporary.chmod(mode)
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return path


def path_value(value):
    if not value:
        return ''
    return str(pathlib.Path(value).expanduser().resolve(strict=True))


def validate_comsol(value):
    value = path_value(value)
    if value and not (pathlib.Path(value) / 'bin/comsol').is_file():
        raise ValueError('COMSOL folder must contain bin/comsol')
    return value


def validate_cuda(value):
    value = path_value(value)
    if not value or not (pathlib.Path(value) / 'lib64/libcudart.so.12').is_file():
        raise ValueError('CUDA folder must contain lib64/libcudart.so.12')
    return value


def init_config(args):
    path = config_file()
    existing = path.is_file()
    values = core.read(path) if existing else core.settings()
    found = []
    if args.comsol is not None:
        values['comsol_root'] = validate_comsol(args.comsol)
    elif not existing and not values.get('comsol_root'):
        found = core.discover_comsol()
        if len(found) == 1:
            values['comsol_root'] = found[0]
    if args.cuda is not None:
        values['cuda_root'] = validate_cuda(args.cuda)
    save_settings(values)
    for name in ['pangu', 'licence', 'runtime']:
        (core.ROOT / name).mkdir(exist_ok=True)
    display({'config': str(path), 'existing_config_preserved': existing,
             'comsol_root': values.get('comsol_root', ''), 'cuda_root': values.get('cuda_root', ''),
             'discovered_installations': found})


def validate_setting(key, parsed):
    if key not in CONFIG_KEYS:
        raise ValueError('Unknown setting. Choose from: ' + ', '.join(sorted(CONFIG_KEYS)))
    if key == 'comsol_root':
        if not isinstance(parsed, str):
            raise ValueError('comsol_root must be a path string')
        parsed = validate_comsol(parsed)
    elif key == 'cuda_root':
        if not isinstance(parsed, str):
            raise ValueError('cuda_root must be a path string')
        parsed = validate_cuda(parsed)
    else:
        if key == 'gui_global_options' and not isinstance(parsed, bool):
            raise ValueError('gui_global_options must be true or false')
        if key in {'licensed_gpu_index', 'ordering'} and (type(parsed) is not int or parsed < 0):
            raise ValueError(key + ' must be a nonnegative integer')
        if key == 'ordering' and parsed not in {0, 1}:
            raise ValueError('ordering must be 0 or 1')
        if key == 'licence_files':
            if not isinstance(parsed, list) or not all(isinstance(n, str) for n in parsed):
                raise ValueError('licence_files must be a JSON list of filenames')
            for name in parsed:
                licence_file(name)
    return parsed


def set_config(key, value):
    parsed = validate_setting(key, value if key in {'comsol_root', 'cuda_root'} else json.loads(value))
    values = core.read(config_file()) if config_file().is_file() else core.settings()
    values[key] = parsed
    save_settings(values)
    display({key: parsed})


def apply_config(value):
    changes = json.loads(value)
    if not isinstance(changes, dict):
        raise ValueError('config apply expects a JSON object')
    validated = {key: validate_setting(key, item) for key, item in changes.items()}
    values = core.read(config_file()) if config_file().is_file() else core.settings()
    values.update(validated)
    save_settings(values)
    display(validated)


def licence_file(name):
    if pathlib.Path(name).name != name or ',' in name or not name.endswith(('.key', '.license')):
        raise ValueError('Use a .key or .license filename from licence/')
    path = core.ROOT / 'licence' / name
    if not path.is_file() or not path.resolve().is_relative_to((core.ROOT / 'licence').resolve()):
        raise ValueError('Licence file not found in licence/: ' + name)
    return path


def list_licences():
    selected = core.settings()['licence_files']
    folder = core.ROOT / 'licence'
    return [{'name': p.name, 'selected': not selected or p.name in selected, 'sha256': core.sha(p)}
            for p in sorted(folder.iterdir() if folder.exists() else [])
            if p.is_file() and p.suffix in {'.key', '.license'}]


def select_licences(names):
    for name in names:
        licence_file(name)
    values = core.read(config_file()) if config_file().is_file() else core.settings()
    values['licence_files'] = list(dict.fromkeys(names))
    save_settings(values)
    display({'licence_files': values['licence_files'], 'automatic_selection': not names})


def package_info():
    try:
        sdk = core.sdk_info()
    except (ValueError, OSError) as error:
        sdk = {'installed': False, 'message': str(error)}
    manifest = core.module_dir() / 'manifest.json'
    component = core.read(manifest) if manifest.is_file() else None
    return {'sdk': sdk, 'module_version': component.get('module_version') if component else None,
            'module_directory': str(core.module_dir())}


def install_package(path, update=False):
    if '://' in str(path):
        raise ValueError('Download the vendor archive yourself, then provide its local path')
    if update:
        core.sdk()
    core.install(path)


def status():
    values = core.settings()
    configured = values.get('comsol_root', '')
    display({'version': VERSION, 'root': str(core.ROOT), 'config': str(config_file()),
             'configured_comsol': configured, 'discovered_comsol': [] if configured else core.discover_comsol(),
             'cuda_root': values['cuda_root'], 'licences': list_licences(), **package_info()})


def parser():
    app = argparse.ArgumentParser(prog='pangu-comsol', description=__doc__)
    app.add_argument('--version', action='version', version='%(prog)s ' + VERSION)
    app.add_argument('--home', type=pathlib.Path, help='SDK/licence/config data directory; separate from the installed app')
    commands = app.add_subparsers(dest='command')
    commands.add_parser('menu', help='Open the interactive terminal application')
    item = commands.add_parser('init', help='Initialize module configuration without resetting existing choices')
    item.add_argument('--comsol', help='COMSOL installation folder')
    item.add_argument('--cuda', help='CUDA folder containing lib64/')
    config = commands.add_parser('config', help='Manage only this module\'s configuration').add_subparsers(dest='action', required=True)
    config.add_parser('show')
    item = config.add_parser('set')
    item.add_argument('key', choices=sorted(CONFIG_KEYS))
    item.add_argument('value')
    item = config.add_parser('apply', help='Atomically apply a JSON object of module settings')
    item.add_argument('value')
    item = commands.add_parser('run', help='Open COMSOL with this module')
    item.add_argument('input', nargs='?', help='Optional MPH model')
    item.add_argument('--threads', type=int)
    item.add_argument('--backend', choices=['pangu', 'cudss'], default='pangu')
    item.add_argument('--execution',choices=['worker','direct'],help='PanguLU execution; default worker. Direct uses a session launcher without libjsig preloading')
    item.add_argument('--detach', action='store_true', help='Start GUI in its own session and return immediately')
    package = commands.add_parser('package', help='Manage manually downloaded, local PanguLU SDK archives').add_subparsers(dest='action', required=True)
    package.add_parser('status')
    for action in ['install', 'update']:
        item = package.add_parser(action)
        item.add_argument('archive', help='Local original vendor .tar.gz file')
    licence = commands.add_parser('license', help='Manage original signed files in licence/').add_subparsers(dest='action', required=True)
    item = licence.add_parser('add');item.add_argument('path')
    item.add_argument('--replace', action='store_true', help='Replace an existing module copy with this original file')
    licence.add_parser('list')
    item = licence.add_parser('use');item.add_argument('names', nargs='+', help='Filenames already imported into licence/')
    licence.add_parser('auto', help='Use all original licence files in licence/')
    item = commands.add_parser('doctor', help='Check SDK, module, GPU and original licence')
    item.add_argument('--output')
    item.add_argument('--json', action='store_true', help='Return the native check as structured JSON')
    commands.add_parser('status', help='Show module configuration and imported licences')
    commands.add_parser('discover', help='Find local COMSOL installations')
    commands.add_parser('build', help='Build production components from the application sources')
    if development.is_development_build():
        from . import testing
        test=commands.add_parser('test',help='Select optional cuDSS calibration backends').add_subparsers(dest='action',required=True)
        test.add_parser('status',help='List native and optional test backends')
        item=test.add_parser('build',help='Build separately provided local test sources')
        item.add_argument('--source',type=pathlib.Path,help='Optional mycudss source directory')
        item=test.add_parser('run',help='Open GUI using a selected test backend')
        item.add_argument('input',nargs='?',help='Optional MPH model; opens a working copy')
        item.add_argument('--backend',choices=list(testing.BACKENDS),default='cudss')
        item.add_argument('--execution',choices=['worker','direct'],help='PanguLU execution mode only')
        item.add_argument('--threads',type=int)
        item.add_argument('--detach',action='store_true')
        item=test.add_parser('benchmark',help='Run the separately provided S numeric acceptance tools')
        item.add_argument('--backend',choices=list(testing.BACKENDS),default='cudss')
        item.add_argument('--execution',choices=['worker','direct'],help='PanguLU execution mode only')
        item.add_argument('--project',type=pathlib.Path,required=True)
        item.add_argument('--frequencies',choices=['20000','all'],default='20000')
        item.add_argument('--threads',type=int,default=8)
        item.add_argument('--repeat',type=int,default=1)
    for action in ['inspect', 'configure', 'restore']:
        item = commands.add_parser(action)
        item.add_argument('input')
        item.add_argument('--output')
        item.add_argument('--target', default='auto')
        item.add_argument('--run', action='store_true')
        item.add_argument('--threads', type=int)
        item.add_argument('--execution',choices=['worker','direct'],help='PanguLU execution mode; default worker')
    return app


def main(argv=None):
    app = parser()
    args = app.parse_args(argv)
    if args.home is not None:
        core.ROOT = args.home.expanduser().resolve()
    if args.command == 'menu' or args.command is None:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            if args.command == 'menu':
                raise ValueError('The terminal menu requires an interactive terminal')
            app.print_help()
            return 0
        try:
            from .tui import PanguComsolApp
        except ModuleNotFoundError as error:
            if error.name == 'textual':
                raise RuntimeError('Install the application dependencies first: pipx install .') from error
            raise
        return PanguComsolApp().run() or 0
    if args.command == 'init':
        init_config(args)
    elif args.command == 'config':
        if args.action == 'show':
            display(core.settings())
        elif args.action == 'apply':
            apply_config(args.value)
        else:
            set_config(args.key, args.value)
    elif args.command == 'package':
        display(package_info()) if args.action == 'status' else install_package(args.archive, args.action == 'update')
    elif args.command == 'license':
        if args.action == 'add':
            core.license_add(args.path, replace=args.replace)
        elif args.action == 'list':
            display(list_licences())
        else:
            select_licences(args.names if args.action == 'use' else [])
    elif args.command == 'doctor':
        report = core.doctor(args.output, native_output=not args.json)
        if args.json:
            display(report)
    elif args.command == 'discover':
        display(core.discover_comsol())
    elif args.command == 'status':
        status()
    elif args.command == 'build':
        core.build()
    elif args.command == 'test':
        from . import testing
        if args.action=='status':display(testing.status())
        elif args.action=='build':display(testing.build(args.source))
        elif args.action=='benchmark':return testing.benchmark(args)
        else:
            args.cmd='run'
            return core.launch(args)
    else:
        args.cmd = args.command
        return core.launch(args)
    return 0


def entrypoint():
    try:
        return main()
    except KeyboardInterrupt:
        print('pangu-comsol: operation cancelled', file=sys.stderr)
        return 130
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print('pangu-comsol: ' + str(error), file=sys.stderr)
        return 1
