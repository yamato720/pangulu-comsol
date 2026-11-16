"""Local keyboard acceptance; never submitted with production source."""
import asyncio
import hashlib
import json
import os
import pathlib
import pty
import select
import struct
import subprocess
import termios
import fcntl
import time
from unittest.mock import patch

from pangulu_comsol import __version__, runtime as core
from pangulu_comsol.tui import PanguComsolApp
from textual.widgets import Button, Select

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = pathlib.Path(json.loads((ROOT / 'data/runs/latest-keyboard-focus.json').read_text())['directory'])
REPORT = {'version': __version__, 'package_path': str(pathlib.Path(core.__file__).resolve()), 'focus': [], 'pty': []}
assert __version__ == '0.4.1'
assert '/site-packages/' in REPORT['package_path']


async def keyboard():
    with patch.object(core, 'ROOT', OUT / 'installed-ui-data'):
        app = PanguComsolApp()
        async with app.run_test(size=(100,36)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app.focused.id == 'initialize'
            for key, expected in [('down','check'), ('down','refresh'), ('up','check')]:
                await pilot.press(key)
                await pilot.pause()
                assert app.focused.id == expected
                marked = [b.id for b in app.query(Button) if str(b.label).startswith('▶ ')]
                assert marked == [expected], marked
                REPORT['focus'].append({'key':key, 'focused':expected, 'marked':marked,
                                        'background':app.focused.styles.background.hex})
            app.save_screenshot('overview.svg', path=str(OUT))
            await pilot.press('f3','down','enter','down','enter')
            await pilot.pause()
            assert app.query_one('#backend',Select).value == 'cudss'
            await pilot.press('down')
            await pilot.pause()
            assert app.focused.id == 'threads'
            app.save_screenshot('launch.svg', path=str(OUT))
            app.exit(0)
        app = PanguComsolApp()
        async with app.run_test(size=(80,24)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.press('f4','down','down','down')
            await pilot.pause(.4)
            assert app.focused.id == 'build-module'
            assert app.focused.parent.parent.scrollable_content_region.contains_region(app.focused.region)
            app.save_screenshot('small-window.svg', path=str(OUT))
            app.exit(0)


def terminal(command, name):
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH',36,100,0,0))
    env = dict(os.environ, TERM='xterm-256color', PYTHONDONTWRITEBYTECODE='1')
    env.pop('PYTHONPATH',None)
    process = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave,
                               cwd=str(OUT), env=env, start_new_session=True)
    os.close(slave)
    capture = bytearray()
    def drain(duration):
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            if select.select([master],[],[],.05)[0]:
                try:
                    data = os.read(master,65536)
                except OSError:
                    break
                if not data:
                    break
                capture.extend(data)
    try:
        drain(1.5)
        for keys in [b'\x1b[B', b'\x1b[B', b'\r', b'\x1bOR', b'\x1b[B', b'\r', b'\x1b[B', b'\r']:
            os.write(master,keys)
            drain(.25)
        os.write(master,b'\x11')
        drain(1)
        code = process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)
    (OUT / (name+'.terminal.log')).write_bytes(capture)
    decoded = capture.decode(errors='replace')
    assert code == 0, code
    assert 'Traceback' not in decoded
    assert '▶' in decoded
    assert 'NVIDIA cuDSS' in decoded
    REPORT['pty'].append({'entry':name,'exit_code':code,'bytes':len(capture),'keys':'down, down, Enter, F3, down, Enter, down, Enter, Ctrl+Q'})


asyncio.run(keyboard())
terminal([str(ROOT/'pangu-comsol'),'--home',str(OUT/'pty-source-data')], 'root-launcher')
terminal([str(ROOT/'runtime/app-env/bin/pangu-comsol'),'--home',str(OUT/'pty-installed-data')], 'installed-console')
wheel=next((OUT/'wheel').glob('*.whl'))
REPORT['wheel']={'path':str(wheel),'sha256':hashlib.sha256(wheel.read_bytes()).hexdigest()}
(OUT/'installed-keyboard-verification.json').write_text(json.dumps(REPORT,ensure_ascii=False,indent=2))
print(json.dumps(REPORT,ensure_ascii=False,indent=2))
