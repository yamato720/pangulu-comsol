import contextlib
import hashlib
import io
import pathlib
import shutil
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[2]/"src/python"))
from pangulu_comsol import runtime as pc


class InstallerTests(unittest.TestCase):
    def setup_root(self, folder):
        root = pathlib.Path(folder) / 'module'
        (root / 'licence').mkdir(parents=True)
        (root / 'licence/customer.key').write_bytes(b'original signed key')
        return root

    def archive(self, folder, version='26.46', payload=b'one', abi=2, bad_hash=False):
        files = {'include/pangulu.h': f'#define PANGULU_VERSION_STRING "{version}"\n#define PANGULU_ABI_VERSION {abi}\n'.encode(),
                 'lib/libpangulu.so': payload}
        files['SHA256SUMS'] = ''.join(('0'*64 if bad_hash else hashlib.sha256(data).hexdigest()) + '  ' + name + '\n'
                                     for name, data in files.items()).encode()
        target = pathlib.Path(folder) / ('sdk-' + hashlib.sha256(files['include/pangulu.h'] + payload).hexdigest()[:12] + '.tar.gz')
        with tarfile.open(target, 'w:gz') as tar:
            for name, data in files.items():
                entry = tarfile.TarInfo('pangulu-release/' + name)
                entry.size = len(data)
                tar.addfile(entry, io.BytesIO(data))
        return target

    def test_whole_sdk_update_preserves_licence_and_uses_new_core(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.setup_root(temp)
            with patch.object(pc, 'ROOT', root), contextlib.redirect_stdout(io.StringIO()):
                pc.install(self.archive(temp, payload=b'first'))
                pc.install(self.archive(temp, version='26.47', payload=b'second'))
                self.assertEqual(pc.sdk_info()['version'], '26.47')
                self.assertEqual((pc.sdk() / 'lib/libpangulu.so').read_bytes(), b'second')
                self.assertEqual((root / 'licence/customer.key').read_bytes(), b'original signed key')
                self.assertEqual(len(list((root / 'pangu').iterdir())), 1)

    def test_invalid_update_leaves_installed_sdk_and_licence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.setup_root(temp)
            with patch.object(pc, 'ROOT', root), contextlib.redirect_stdout(io.StringIO()):
                pc.install(self.archive(temp, payload=b'accepted'))
                for archive in [self.archive(temp, payload=b'bad', bad_hash=True), self.archive(temp, payload=b'new-abi', abi=3)]:
                    with self.assertRaises(ValueError):
                        pc.install(archive)
                    self.assertEqual((pc.sdk() / 'lib/libpangulu.so').read_bytes(), b'accepted')
                    self.assertEqual((root / 'licence/customer.key').read_bytes(), b'original signed key')

    def test_manual_extract_needs_no_activation_or_registration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.setup_root(temp)
            with patch.object(pc, 'ROOT', root), contextlib.redirect_stdout(io.StringIO()):
                pc.install(self.archive(temp))
                shutil.rmtree(root / 'pangu')
                (root / 'pangu').mkdir()
                with tarfile.open(self.archive(temp, version='26.48', payload=b'manual')) as tar:
                    tar.extractall(root / 'pangu', filter='data')
                self.assertTrue(pc.verify(pc.sdk()))
                self.assertEqual(pc.sdk_info()['version'], '26.48')
                self.assertEqual((root / 'licence/customer.key').read_bytes(), b'original signed key')

    def test_commit_failure_restores_previous_sdk(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.setup_root(temp)
            with patch.object(pc, 'ROOT', root), contextlib.redirect_stdout(io.StringIO()):
                pc.install(self.archive(temp, payload=b'accepted'))
                with patch.object(pc, 'write', side_effect=OSError('simulated write failure')):
                    with self.assertRaises(OSError):
                        pc.install(self.archive(temp, version='26.47', payload=b'new'))
                self.assertEqual((pc.sdk() / 'lib/libpangulu.so').read_bytes(), b'accepted')
                self.assertEqual((root / 'licence/customer.key').read_bytes(), b'original signed key')

    def test_multiple_sdks_are_not_silently_selected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.setup_root(temp)
            with patch.object(pc, 'ROOT', root), contextlib.redirect_stdout(io.StringIO()):
                pc.install(self.archive(temp))
                shutil.copytree(pc.sdk(), root / 'pangu/another-release')
                with self.assertRaises(ValueError):
                    pc.sdk()

    def test_archive_cannot_escape_module(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.setup_root(temp)
            target = pathlib.Path(temp) / 'escape.tar.gz'
            with tarfile.open(target, 'w:gz') as tar:
                entry = tarfile.TarInfo('../../outside')
                entry.size = 1
                tar.addfile(entry, io.BytesIO(b'x'))
            with patch.object(pc, 'ROOT', root):
                with self.assertRaises(tarfile.FilterError):
                    pc.install(target)
                self.assertEqual((root / 'licence/customer.key').read_bytes(), b'original signed key')

    def test_unsupported_header_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.setup_root(temp)
            with patch.object(pc, 'ROOT', root):
                with self.assertRaises(ValueError):
                    pc.install(self.archive(temp, version='../../outside'))


if __name__ == '__main__':
    unittest.main()
