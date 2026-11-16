import contextlib
import io
import json
import pathlib
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[2]/'src/python'))
from pangulu_comsol import cli, testing
from pangulu_comsol import runtime as core
from pangulu_comsol.tui import PanguComsolApp
from textual.widgets import Select, TabbedContent, Button


class OptionalBackendTests(unittest.TestCase):
    def fixture(self,folder):
        root=pathlib.Path(folder);comsol=root/'comsol';(comsol/'bin').mkdir(parents=True);(comsol/'bin/comsol').write_text('fixture')
        native=comsol/'ext/cudss/glnxa64/libcudss.so.0.7.1';native.parent.mkdir(parents=True);native.write_bytes(b'original native fixture')
        (root/'config').mkdir();(root/'config/runtime.json').write_text(json.dumps({'comsol_root':str(comsol),'cuda_root':str(root/'cuda')}))
        target=root/'runtime/tests/mycudss';(target/'direct').mkdir(parents=True);(target/'worker').mkdir()
        for name in ['direct/libcudss.so.0.7.1','worker/libcudss.so.0.7.1','worker/mycudss_worker']:(target/name).write_bytes(name.encode())
        value={'version':1,'native_library_sha256':core.sha(native),'source_directory':str(root/'optional-sources'),
               'files':{str(p.relative_to(target)):core.sha(p) for p in target.rglob('*') if p.is_file()}}
        core.write(target/'manifest.json',value)
        return root,target,native

    def test_test_backends_do_not_require_or_load_Pangu_or_change_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            root,target,native=self.fixture(folder);before=(root/'config/runtime.json').read_bytes()
            with patch.object(core,'ROOT',root):
                for backend in ['mycudss-direct','mycudss-worker']:
                    command,env,module=core.comsol_command(root/backend,backend=backend)
                    self.assertEqual(command[command.index('-cudssroot')+1],str(target/backend.removeprefix('mycudss-')))
                    self.assertNotIn('PANGULU_LICENSE_PATHS',env)
                    self.assertNotIn('PC_WORKER',env)
                    self.assertFalse(set(command)&{'-prefsdir','-tmpdir','-configuration','-data','-np','-gpuid'})
                    if backend.endswith('worker'):
                        self.assertEqual(env['MYCUDSS_WORKER'],str(target/'worker/mycudss_worker'))
                        self.assertTrue(env['MYCUDSS_WORKER_LD_LIBRARY_PATH'].startswith(str(native.parent)))
                    else:self.assertEqual(env['MYCUDSS_LIBRARY'],str(native))
                self.assertTrue(all(item['available'] for item in testing.status()['backends'] if item['id']!='pangu'))
            self.assertEqual((root/'config/runtime.json').read_bytes(),before)
            self.assertFalse((root/'pangu').exists())

    def test_corrupted_test_component_is_rejected_before_launch(self):
        with tempfile.TemporaryDirectory() as folder:
            root,target,native=self.fixture(folder);(target/'worker/mycudss_worker').write_bytes(b'changed')
            with patch.object(core,'ROOT',root),self.assertRaisesRegex(ValueError,'changed or missing'):
                testing.environment('mycudss-worker')

    def test_build_failure_preserves_previous_test_components_and_log(self):
        with tempfile.TemporaryDirectory() as folder:
            root,target,native=self.fixture(folder);before=(target/'manifest.json').read_bytes()
            source=root/'sources';source.mkdir()
            for name in ['direct.cpp','bridge.cpp','worker.cpp','protocol.h','profile.h']:(source/name).write_text('fixture')
            with patch.object(core,'ROOT',root),patch.object(testing.subprocess,'run',side_effect=subprocess.CalledProcessError(1,['compiler'])):
                with self.assertRaisesRegex(RuntimeError,'build failed; see'):testing.build(source)
            self.assertEqual((target/'manifest.json').read_bytes(),before)
            self.assertEqual(len(list((root/'data/runs').glob('test-build_*/build.log'))),1)
            self.assertFalse(list(target.parent.glob('.mycudss-build-*')))

    def test_cli_test_run_dispatches_explicit_backend(self):
        with patch.object(core,'launch',return_value=0) as launch:
            self.assertEqual(cli.main(['test','run','--backend','mycudss-direct','--detach']),0)
        args=launch.call_args.args[0];self.assertEqual(args.cmd,'run');self.assertTrue(args.detach);self.assertEqual(args.backend,'mycudss-direct')


class OptionalMenuTests(unittest.IsolatedAsyncioTestCase):
    async def test_keyboard_selects_test_backend_without_changing_normal_launch(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(core,'ROOT',pathlib.Path(folder)):
            app=PanguComsolApp()
            async with app.run_test(size=(100,36)) as pilot:
                await app.workers.wait_for_complete();await pilot.pause()
                await pilot.press('f8','enter','down','down','enter');await pilot.pause()
                self.assertEqual(app.query_one('#pages',TabbedContent).active,'tests')
                self.assertEqual(app.query_one('#test-backend',Select).value,'mycudss-worker')
                self.assertEqual(app.query_one('#backend',Select).value,'pangu')
                with patch.object(app,'request_operation') as request:
                    app.query_one('#launch-test',Button).press();await pilot.pause()
                    self.assertEqual(request.call_args.args[0],['test','run','--backend','mycudss-worker','--detach'])
                app.exit(0)
