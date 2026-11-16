import asyncio
import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[2]/'src/python'))
from pangulu_comsol import cli, paths
from pangulu_comsol import runtime as core
from pangulu_comsol.tui import PanguComsolApp
from textual.widgets import Button, Input, SelectionList, Switch, Select, Tabs, TabbedContent
import test_installer


class ApplicationPathsTests(unittest.TestCase):
    def test_home_option_applies_before_initializing(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(core,'ROOT',core.ROOT),patch.object(core,'discover_comsol',return_value=[]),contextlib.redirect_stdout(io.StringIO()):
            target=pathlib.Path(folder)/'application data'
            cli.main(['--home',str(target),'init'])
            self.assertTrue((target/'config/runtime.json').is_file())

    def test_installed_default_uses_data_directory_not_package_directory(self):
        with patch.object(paths,'source_project',return_value=None),patch.dict(os.environ,{'XDG_DATA_HOME':'/chosen/data'},clear=True):
            self.assertEqual(paths.application_home(),pathlib.Path('/chosen/data/pangu-comsol'))

    def test_config_apply_validates_every_field_before_write(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(core,'ROOT',pathlib.Path(folder)):
            cli.save_settings({'ordering':0,'gui_global_options':True,'keep':'original'})
            before=cli.config_file().read_bytes()
            with self.assertRaises(ValueError):cli.apply_config('{"gui_global_options":false,"ordering":8}')
            self.assertEqual(cli.config_file().read_bytes(),before)
            with contextlib.redirect_stdout(io.StringIO()):cli.apply_config('{"gui_global_options":false,"ordering":1}')
            self.assertEqual(core.read(cli.config_file()),{'ordering':1,'gui_global_options':False,'keep':'original'})


class TerminalMenuTests(unittest.IsolatedAsyncioTestCase):
    async def test_arrows_focus_controls_preserve_editing_and_activate_buttons(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(core,'ROOT',pathlib.Path(folder)):
            app=PanguComsolApp()
            async with app.run_test(size=(100,36)) as pilot:
                await app.workers.wait_for_complete();await pilot.pause()
                self.assertEqual(app.focused.id,'initialize')
                self.assertTrue(str(app.focused.label).startswith('▶ '))
                self.assertTrue(all(b.variant=='default' for b in app.query(Button)))
                focused_color=app.focused.styles.background
                await pilot.press('down');await pilot.pause()
                self.assertEqual(app.focused.id,'check')
                self.assertFalse(str(app.query_one('#initialize',Button).label).startswith('▶ '))
                self.assertNotEqual(app.query_one('#initialize',Button).styles.background,focused_color)
                await pilot.press('up','enter');await app.workers.wait_for_complete();await pilot.pause()
                self.assertEqual(app.last_result['returncode'],0)
                self.assertTrue((pathlib.Path(folder)/'config/runtime.json').is_file())
                await pilot.press('f2');await pilot.pause()
                self.assertEqual(app.focused.id,'comsol-path')
                await pilot.press('a','b','c','left','X');await pilot.pause()
                self.assertEqual(app.query_one('#comsol-path',Input).value,'abXc')
                await pilot.press('down');await pilot.pause()
                self.assertEqual(app.focused.id,'cuda-path')
                await pilot.press('down','space');await pilot.pause()
                self.assertEqual(app.focused.id,'global-options')
                self.assertFalse(app.query_one('#global-options',Switch).value)
                await pilot.press('tab');await pilot.pause()
                self.assertEqual(app.focused.id,'save-config')
                await pilot.press('shift+tab');await pilot.pause()
                self.assertEqual(app.focused.id,'global-options')
                await pilot.press('escape','right');await pilot.pause()
                self.assertIsInstance(app.focused,Tabs)
                self.assertEqual(app.query_one('#pages',TabbedContent).active,'launch')
                await pilot.press('down');await pilot.pause()
                self.assertEqual(app.focused.id,'model-path')
                await pilot.press('f1','down','down','down','enter');await pilot.pause()
                self.assertEqual(type(app.screen).__name__,'Details')
                await pilot.press('escape');await pilot.pause()
                self.assertEqual(app.focused.id,'details')
                app.exit(0)

    async def test_dropdown_and_licence_list_keep_arrow_selection(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(core,'ROOT',pathlib.Path(folder)):
            app=PanguComsolApp()
            async with app.run_test(size=(100,36)) as pilot:
                await app.workers.wait_for_complete();await pilot.pause()
                await pilot.press('f3','down');await pilot.pause()
                backend=app.query_one('#backend',Select)
                self.assertIs(app.focused,backend)
                self.assertFalse(backend.expanded)
                await pilot.press('enter','down','enter');await pilot.pause()
                self.assertEqual(backend.value,'cudss')
                self.assertIs(app.focused,backend)
                await pilot.press('enter','up','escape');await pilot.pause()
                self.assertFalse(backend.expanded)
                self.assertEqual(backend.value,'cudss')
                await pilot.press('down');await pilot.pause()
                self.assertEqual(app.focused.id,'threads')
                await pilot.press('f5','down','down','down');await pilot.pause()
                choices=app.query_one('#licence-list',SelectionList)
                self.assertIs(app.focused,choices)
                await pilot.press('down');await pilot.pause()
                self.assertEqual(app.focused.id,'select-licences')
                choices.add_options([('a.key','a.key',False),('b.key','b.key',False)])
                await pilot.press('up');await pilot.pause()
                self.assertIs(app.focused,choices)
                choices.highlighted=0
                await pilot.press('space','down');await pilot.pause()
                self.assertIs(app.focused,choices)
                self.assertEqual(choices.highlighted,1)
                self.assertEqual(choices.selected,['a.key'])
                await pilot.press('space','down');await pilot.pause()
                self.assertEqual(app.focused.id,'select-licences')
                self.assertEqual(choices.selected,['a.key','b.key'])
                app.exit(0)

    async def test_small_terminal_arrows_scroll_to_focus_and_skip_disabled(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(core,'ROOT',pathlib.Path(folder)):
            app=PanguComsolApp()
            async with app.run_test(size=(80,24)) as pilot:
                await app.workers.wait_for_complete();await pilot.pause()
                app.query_one('#check',Button).disabled=True
                await pilot.press('down');await pilot.pause()
                self.assertEqual(app.focused.id,'refresh')
                await pilot.press('f4');await pilot.pause()
                await pilot.press('down','down','down');await pilot.pause(.4)
                self.assertEqual(app.focused.id,'build-module')
                self.assertTrue(app.screen.region.contains_region(app.focused.region))
                self.assertTrue(app.focused.parent.parent.scrollable_content_region.contains_region(app.focused.region))
                self.assertTrue(str(app.focused.label).startswith('▶ '))
                app.exit(0)

    async def press(self,app,selector):
        app.query_one(selector,Button).press()
        await asyncio.sleep(.05)
        await app.workers.wait_for_complete()
        await asyncio.sleep(.05)
        self.assertFalse(app.busy)
        self.assertIsNotNone(app.last_result)
        self.assertEqual(app.last_result['returncode'],0,app.last_result)

    async def test_menu_initializes_config_imports_and_selects_licence(self):
        with tempfile.TemporaryDirectory() as folder:
            target=pathlib.Path(folder)/'application data';key=pathlib.Path(folder)/'original.key';key.write_bytes(b'original signed fixture')
            comsol=pathlib.Path(folder)/'COMSOL';(comsol/'bin').mkdir(parents=True);(comsol/'bin/comsol').write_text('fixture')
            cuda=pathlib.Path(folder)/'CUDA';(cuda/'lib64').mkdir(parents=True);(cuda/'lib64/libcudart.so.12').write_text('fixture')
            with patch.object(core,'ROOT',target):
                app=PanguComsolApp()
                async with app.run_test(size=(120,44)) as pilot:
                    await app.workers.wait_for_complete();await pilot.pause()
                    self.assertFalse((target/'config/runtime.json').exists())
                    await self.press(app,'#initialize')
                    app.action_show_tab('configuration');await pilot.pause()
                    app.query_one('#comsol-path',Input).value=str(comsol)
                    app.query_one('#cuda-path',Input).value=str(cuda)
                    app.query_one('#global-options',Switch).value=False
                    await self.press(app,'#save-config')
                    value=core.read(target/'config/runtime.json');self.assertEqual(value['comsol_root'],str(comsol));self.assertFalse(value['gui_global_options'])
                    app.action_show_tab('licences');await pilot.pause()
                    app.query_one('#licence-path',Input).value=str(key)
                    await self.press(app,'#import-licence')
                    self.assertEqual((target/'licence/original.key').read_bytes(),key.read_bytes())
                    self.assertEqual(app.query_one('#licence-list',SelectionList).selected,['original.key'])
                    await self.press(app,'#select-licences')
                    self.assertEqual(core.read(target/'config/runtime.json')['licence_files'],['original.key'])
                    await self.press(app,'#auto-licences')
                    self.assertEqual(core.read(target/'config/runtime.json')['licence_files'],[])
                    app.exit(0)

    async def test_cancel_build_preserves_existing_components(self):
        with tempfile.TemporaryDirectory() as folder:
            target=pathlib.Path(folder)/'application';target.mkdir()
            comsol=target/'comsol';(comsol/'bin').mkdir(parents=True);(comsol/'bin/comsol').write_text('fixture')
            tools=target/'tools';tools.mkdir();marker=target/'compiler-started'
            compiler=tools/'gcc';compiler.write_text('#!/bin/sh\ntouch "'+str(marker)+'"\nexec sleep 30\n');compiler.chmod(0o755)
            archive=test_installer.InstallerTests().archive(folder)
            with patch.object(core,'ROOT',target),patch.dict(os.environ,{'PATH':str(tools)+os.pathsep+os.environ['PATH']}):
                with contextlib.redirect_stdout(io.StringIO()):core.install(archive)
                cli.save_settings({'comsol_root':str(comsol),'cuda_root':'cuda','licence_files':[]})
                previous=target/'runtime/module';previous.mkdir();(previous/'manifest.json').write_text('{"module_version":"previous"}')
                app=PanguComsolApp()
                async with app.run_test(size=(100,32)) as pilot:
                    await app.workers.wait_for_complete();await pilot.pause()
                    app.request_operation(['build'],'build')
                    for _ in range(100):
                        if marker.exists():break
                        await asyncio.sleep(.03)
                    self.assertTrue(marker.exists())
                    app.action_cancel();await app.workers.wait_for_complete();await pilot.pause()
                    self.assertEqual(app.last_result['returncode'],130)
                    self.assertEqual((previous/'manifest.json').read_text(),'{"module_version":"previous"}')
                    self.assertFalse(list((target/'runtime').glob('.module-build-*')))
                    app.exit(0)

    async def test_failed_menu_operation_preserves_config_and_allows_retry(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(core,'ROOT',pathlib.Path(folder)):
            cli.save_settings({'comsol_root':'','cuda_root':'runtime/cuda','ordering':0,'licence_files':[]})
            before=cli.config_file().read_bytes();app=PanguComsolApp()
            async with app.run_test(size=(80,24)) as pilot:
                await app.workers.wait_for_complete();await pilot.pause()
                app.request_operation(['config','apply','{"ordering":8}'],'invalid setting')
                await asyncio.sleep(.05);await app.workers.wait_for_complete();await pilot.pause()
                self.assertEqual(app.last_result['returncode'],1)
                self.assertEqual(cli.config_file().read_bytes(),before)
                self.assertFalse(app.query_one('#initialize',Button).disabled)
                app.action_show_tab('launch');await pilot.pause()
                app.query_one('#threads',Input).value='0'
                app.query_one('#launch-gui',Button).press();await pilot.pause()
                self.assertFalse(app.busy)
                app.exit(0)
