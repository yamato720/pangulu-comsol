import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/python'))
from pangulu_comsol import cli, development, testing
from pangulu_comsol import runtime as core
from pangulu_comsol.tui import PanguComsolApp
from textual.widgets import TabPane, TabbedContent


class BuildFlavorTests(unittest.TestCase):
    def test_user_cli_has_no_test_command_and_keeps_normal_backends(self):
        with patch.object(development, 'is_development_build', return_value=False):
            parser = cli.parser()
            self.assertNotIn('test', parser._subparsers._group_actions[0].choices)
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parser.parse_args(['test', 'status'])
            self.assertEqual(parser.parse_args(['run', '--backend', 'cudss']).backend, 'cudss')

    def test_developer_cli_retains_calibration(self):
        with patch.object(development, 'is_development_build', return_value=True):
            self.assertEqual(cli.parser().parse_args(['test', 'status']).command, 'test')


class UserMenuTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_has_five_pages_no_f8_and_never_probes_test_components(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(core, 'ROOT', Path(folder)), \
             patch.object(development, 'is_development_build', return_value=False), \
             patch.object(testing, 'status', side_effect=AssertionError('Customer build accessed tests')):
            app = PanguComsolApp()
            async with app.run_test(size=(100, 36)) as pilot:
                await app.workers.wait_for_complete()
                self.assertEqual(len(app.query(TabPane)), 5)
                self.assertFalse(app.query('#tests'))
                self.assertNotIn('f8', app._bindings.key_to_bindings)
                app.action_show_tab('tests')
                self.assertEqual(app.query_one('#pages', TabbedContent).active, 'overview')
                await pilot.press('f3')
                self.assertEqual(app.query_one('#pages', TabbedContent).active, 'launch')
                app.exit(0)
