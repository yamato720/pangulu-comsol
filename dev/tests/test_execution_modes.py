import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src/python'))
from pangulu_comsol import cli,execution
from pangulu_comsol import runtime as core
from pangulu_comsol.tui import PanguComsolApp
from textual.widgets import Select,Button


class ExecutionTests(unittest.TestCase):
    def test_private_launcher_changes_only_jsig_line_and_preserves_vendor_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);comsol=root/'installed';(comsol/'bin').mkdir(parents=True)
            text='#!/bin/bash\necho keep-everything\n  setflldpreload ${JREROOT}/lib/libjsig.so\nexit 0\n'
            source=comsol/'bin/comsol';source.write_text(text)
            with patch.object(core,'ROOT',root),patch.object(core,'cfg',return_value={'comsol_root':str(comsol)}):
                path,info=execution.direct_launcher()
            self.assertEqual(source.read_text(),text)
            self.assertEqual(path.read_text(),text.replace('setflldpreload ${JREROOT}/lib/libjsig.so',': # PanguLU direct session: omit libjsig preloading'))
            self.assertFalse(info['installation_modified'])
            self.assertFalse(list(path.parent.glob('.direct-*')))

    def test_unknown_launcher_is_rejected_without_a_partial_script(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);comsol=root/'installed';(comsol/'bin').mkdir(parents=True);(comsol/'bin/comsol').write_text('#!/bin/bash\n')
            with patch.object(core,'ROOT',root),patch.object(core,'cfg',return_value={'comsol_root':str(comsol)}),self.assertRaisesRegex(ValueError,'not recognized'):
                execution.direct_launcher()
            self.assertFalse((root/'runtime/launchers/comsol-direct').exists())

    def test_execution_is_explicit_and_not_applied_to_other_backends(self):
        args=cli.parser().parse_args(['run','--execution','direct']);self.assertEqual(args.backend,'pangu');self.assertEqual(args.execution,'direct')
        args=cli.parser().parse_args(['run']);self.assertIsNone(args.execution)
        with self.assertRaisesRegex(ValueError,'only to the PanguLU'):
            core.comsol_command(Path('/unused'),'cudss',backend='cudss',execution='direct')
        with self.assertRaisesRegex(ValueError,'worker or direct'):core.clean_env(execution='unknown')


class ExecutionMenuTests(unittest.IsolatedAsyncioTestCase):
    async def test_main_and_test_pages_expose_Pangu_execution_without_saving_settings(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(core,'ROOT',Path(temp)):
            app=PanguComsolApp()
            async with app.run_test(size=(110,40)) as pilot:
                await app.workers.wait_for_complete();await pilot.pause()
                await pilot.press('f3');await pilot.pause()
                self.assertFalse(app.query_one('#execution',Select).disabled)
                app.query_one('#execution',Select).value='direct'
                with patch.object(app,'request_operation') as request:
                    app.query_one('#launch-gui',Button).press();await pilot.pause()
                    self.assertIn('direct',request.call_args.args[0]);self.assertIn('--execution',request.call_args.args[0])
                app.query_one('#backend',Select).value='cudss';await pilot.pause()
                self.assertTrue(app.query_one('#execution',Select).disabled)
                await pilot.press('f8');await pilot.pause()
                app.query_one('#test-backend',Select).value='pangu';await pilot.pause()
                self.assertFalse(app.query_one('#test-execution',Select).disabled)
                app.query_one('#test-execution',Select).value='direct'
                with patch.object(app,'request_operation') as request:
                    app.query_one('#launch-test',Button).press();await pilot.pause()
                    self.assertEqual(request.call_args.args[0],['test','run','--backend','pangu','--execution','direct','--detach'])
                self.assertFalse((Path(temp)/'config/runtime.json').exists());app.exit(0)
