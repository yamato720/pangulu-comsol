import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[2]/'src/python'))
from pangulu_comsol import cli
from pangulu_comsol import runtime as core
import test_installer


class CliTests(unittest.TestCase):
    def setup_root(self,temp):
        root=pathlib.Path(temp)/'module';(root/'config').mkdir(parents=True)
        (root/'config/distribution.json').write_text(json.dumps({'comsol_root':'','cuda_root':'runtime/cuda','licence_files':[]}))
        return root

    def invoke(self,args):
        with contextlib.redirect_stdout(io.StringIO()):return cli.main(args)

    def test_init_discovers_single_installation_and_preserves_existing_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=self.setup_root(temp)
            with patch.object(core,'ROOT',root),patch.object(core,'discover_comsol',return_value=['/chosen/comsol']):
                self.invoke(['init']);self.assertEqual(core.read(root/'config/runtime.json')['comsol_root'],'/chosen/comsol')
                raw=b'{"comsol_root":"/my/custom/comsol","cuda_root":"my-cuda","my_option":19,"licence_files":[]}'
                (root/'config/runtime.json').write_bytes(raw);self.invoke(['init'])
                self.assertEqual((root/'config/runtime.json').read_bytes(),raw)
                self.assertFalse((root/'data').exists())

    def test_explicit_init_option_changes_only_requested_choice(self):
        with tempfile.TemporaryDirectory() as temp:
            root=self.setup_root(temp);cuda=root/'chosen-cuda';(cuda/'lib64').mkdir(parents=True);(cuda/'lib64/libcudart.so.12').write_bytes(b'lib')
            original={'comsol_root':'/keep/comsol','cuda_root':'old','licence_files':[],'custom':'keep'}
            core.write(root/'config/runtime.json',original)
            with patch.object(core,'ROOT',root):self.invoke(['init','--cuda',str(cuda)])
            changed=core.read(root/'config/runtime.json');self.assertEqual(changed,{**original,'cuda_root':str(cuda)})

    def test_invalid_configuration_does_not_replace_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root=self.setup_root(temp);core.write(root/'config/runtime.json',{'comsol_root':'keep'})
            before=(root/'config/runtime.json').read_bytes()
            with patch.object(core,'ROOT',root):
                for args in [['config','set','ordering','2'],['config','set','gui_global_options','1']]:
                    with self.assertRaises(ValueError):self.invoke(args)
            self.assertEqual((root/'config/runtime.json').read_bytes(),before)

    def test_config_write_failure_keeps_previous_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root=self.setup_root(temp);core.write(root/'config/runtime.json',{'ordering':0})
            before=(root/'config/runtime.json').read_bytes()
            with patch.object(core,'ROOT',root),patch.object(pathlib.Path,'replace',side_effect=OSError('simulated')):
                with self.assertRaises(OSError):self.invoke(['config','set','ordering','1'])
            self.assertEqual((root/'config/runtime.json').read_bytes(),before)
            self.assertFalse(list((root/'config').glob('.config-*')))

    def test_import_select_and_explicit_replace_licence(self):
        with tempfile.TemporaryDirectory() as temp:
            root=self.setup_root(temp);source=pathlib.Path(temp)/'customer.key';source.write_bytes(b'original signed contents')
            other=pathlib.Path(temp)/'second.key';other.write_bytes(b'second original')
            with patch.object(core,'ROOT',root):
                self.invoke(['license','add',str(source)]);self.invoke(['license','add',str(other)])
                self.invoke(['license','use','second.key']);self.assertEqual([p.name for p in core.licence_paths()],['second.key'])
                self.invoke(['license','auto']);self.assertEqual(len(core.licence_paths()),2)
                source.write_bytes(b'renewed original')
                with self.assertRaises(ValueError):self.invoke(['license','add',str(source)])
                self.invoke(['license','add',str(source),'--replace'])
                self.assertEqual((root/'licence/customer.key').read_bytes(),source.read_bytes())
                self.assertEqual((root/'licence/customer.key').stat().st_mode&0o777,0o600)
                with self.assertRaises(ValueError):self.invoke(['license','use','../customer.key'])

    def test_package_commands_install_update_and_preserve_licence(self):
        helper=test_installer.InstallerTests()
        with tempfile.TemporaryDirectory() as temp:
            root=self.setup_root(temp);(root/'licence').mkdir();(root/'licence/customer.key').write_bytes(b'keep original key')
            first=helper.archive(temp,payload=b'first');second=helper.archive(temp,version='26.47',payload=b'second')
            with patch.object(core,'ROOT',root):
                self.invoke(['package','install',str(first)]);self.invoke(['package','update',str(second)])
                self.assertEqual(core.sdk_info()['version'],'26.47')
                self.assertEqual((root/'licence/customer.key').read_bytes(),b'keep original key')
                with self.assertRaises(ValueError):self.invoke(['package','install','https://example.com/sdk.tar.gz'])

    def test_run_dispatches_to_runtime_GUI(self):
        with patch.object(core,'launch',return_value=0) as launch:
            self.assertEqual(self.invoke(['run','model.mph','--threads','4']),0)
            args=launch.call_args.args[0];self.assertEqual(args.cmd,'run');self.assertEqual(args.input,'model.mph');self.assertEqual(args.threads,4)


if __name__=='__main__':unittest.main()
