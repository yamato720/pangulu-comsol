import json
import os
import pathlib
import tempfile
import types
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[2]/"src/python"))
from pangulu_comsol import runtime as pc


class StartupSettingsTests(unittest.TestCase):
    def root(self, path):
        root=pathlib.Path(path)/'module'
        (root/'config').mkdir(parents=True)
        comsol=root/'installed-comsol'
        (comsol/'bin/glnxa64').mkdir(parents=True)
        (comsol/'bin/comsol').write_text('')
        library=comsol/'ext/cudss/glnxa64/libcudss.so.0.7.1';library.parent.mkdir(parents=True);library.write_bytes(b'library')
        (comsol/'bin/glnxa64/comsol.ini').write_text('-Dosgi.bundles=existing.custom.bundle@3:start\n')
        (root/'config/runtime.json').write_text(json.dumps({'comsol_root':str(comsol),'cuda_root':'cuda','gui_global_options':True}))
        return root

    def test_default_preserves_existing_thread_affinity_and_directory_environment(self):
        choices={'OMP_NUM_THREADS':'5','MKL_NUM_THREADS':'6','OPENBLAS_NUM_THREADS':'7','OMP_DYNAMIC':'TRUE',
                 'OMP_PLACES':'cores','KMP_AFFINITY':'compact','PREFSDIR':'chosen-prefs','COMSOL_TMPDIR':'chosen-temp'}
        with patch.dict(os.environ,choices):
            result=pc.base_env()
        for key,value in choices.items():self.assertEqual(result[key],value)

    def test_explicit_threads_change_only_thread_counts(self):
        with patch.dict(os.environ,{'OMP_NUM_THREADS':'5','OMP_DYNAMIC':'TRUE','OMP_PLACES':'cores'}):
            result=pc.base_env(3)
        self.assertEqual(result['OMP_NUM_THREADS'],'3')
        self.assertEqual(result['OMP_DYNAMIC'],'TRUE')
        self.assertEqual(result['OMP_PLACES'],'cores')

    def test_command_creates_no_COMSOL_preferences_or_directory_overrides(self):
        with tempfile.TemporaryDirectory() as temp:
            root=self.root(temp);saved=root/'user-settings';saved.write_text('retain my existing choices')
            with patch.object(pc,'ROOT',root):
                command,env,module=pc.comsol_command(root/'session',backend='cudss')
            self.assertFalse(set(command)&{'-prefsdir','-tmpdir','-recoverydir','-configuration','-data','-np','-gpuid'})
            self.assertEqual(saved.read_text(),'retain my existing choices')
            self.assertEqual(sorted(p.name for p in (root/'session').iterdir()),['session.json'])

    def test_GUI_extends_existing_bundle_list_without_redirecting_COMSOL(self):
        with tempfile.TemporaryDirectory() as temp:
            root=self.root(temp);captured=[]
            def command(session,threads,backend):
                pc.write(session/'session.json',{'backend':backend})
                return ['comsol'],{},root/'runtime/module'
            with patch.object(pc,'ROOT',root),patch.object(pc,'comsol_command',side_effect=command),patch.object(pc.subprocess,'call',side_effect=lambda cmd,env:captured.append(cmd) or 0):
                pc.launch(types.SimpleNamespace(cmd='run',input=None,backend='pangu',threads=None))
            args=captured[0];bundles=next(x for x in args if x.startswith('-Dosgi.bundles='))
            self.assertTrue(bundles.startswith('-Dosgi.bundles=existing.custom.bundle@3:start,reference:'))
            self.assertIn('PanguDesktop.jar',bundles)
            self.assertFalse(set(args)&{'-prefsdir','-tmpdir','-recoverydir','-configuration','-data','-np','-gpuid'})
            self.assertFalse(any(x.startswith(('-Dosgi.configuration.area=','-Dosgi.instance.area=')) for x in args))


if __name__=='__main__':unittest.main()
