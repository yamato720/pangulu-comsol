#!/usr/bin/env python3
"""GUI execution smoke in copied test preferences; production startup is unchanged."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import types
import zipfile

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'src/python'))
from pangulu_comsol import runtime as core
OUT=Path(json.loads((ROOT/'data/runs/latest-pangu-execution.json').read_text())['directory'])


def main():
    comsol=Path(core.cfg()['comsol_root']);classes=OUT/'gui-java';classes.mkdir()
    subprocess.run([str(comsol/'java/glnxa64/jre/bin/javac'),'-proc:none','-classpath',str(comsol/'plugins/*'),'-d',str(classes),
                    str(ROOT/'dev/tests/java/ExecutionGuiCheck.java')],check=True)
    bundle=OUT/'ExecutionGuiCheck.jar'
    manifest='Manifest-Version: 1.0\nBundle-ManifestVersion: 2\nBundle-SymbolicName: org.pangulu.verify.execution\nBundle-Version: 0.1.0\nBundle-Activator: org.pangulu.verify.ExecutionGuiCheck\nImport-Package: org.osgi.framework\nRequire-Bundle: com.comsol.api,com.comsol.model,com.comsol.gui,com.comsol.guimph,org.eclipse.swt\n\n'
    with zipfile.ZipFile(bundle,'w') as archive:
        archive.writestr('META-INF/MANIFEST.MF',manifest)
        for path in classes.rglob('*.class'):archive.write(path,str(path.relative_to(classes)))
    original_command=core.comsol_command;original_bundles=core.desktop_bundles
    reference=OUT/'integration-worker/real_cpu.mph';before=core.sha(reference);reports=[]
    user_prefs={str(path):core.sha(path) for path in (Path.home()/'.comsol/v64').glob('*.prefs')}
    for mode in ['worker','direct']:
        home=OUT/('gui-'+mode+'-profile');prefs=home/'.comsol/v64';prefs.mkdir(parents=True)
        for path in (Path.home()/'.comsol/v64').glob('*.prefs'):shutil.copy2(path,prefs/path.name)
        result=OUT/('gui-'+mode+'.json')
        def command(session,threads=None,backend='pangu',execution=None):
            args,env,module=original_command(session,threads,backend,execution)
            # Test-only isolation; the public launcher never supplies these overrides.
            env['PREFSDIR']=str(prefs)
            args+=['-Duser.home='+str(home),'-Dverify.execution.output='+result.as_uri(),'-3drend','sw']
            return args,env,module
        core.comsol_command=command;core.desktop_bundles=lambda:original_bundles()+',reference:'+bundle.as_uri()+'@5:start'
        capture=io.StringIO()
        with contextlib.redirect_stdout(capture):core.launch(types.SimpleNamespace(cmd='run',input=str(reference),backend='pangu',execution=mode,threads=8,detach=True))
        started=json.loads(capture.getvalue().splitlines()[-1]);pid=started['pid'];seen={};deadline=time.monotonic()+120
        print('GUI START',mode,pid,flush=True)
        try:
            while time.monotonic()<deadline:
                for path in Path('/proc').iterdir():
                    if not path.name.isdecimal():continue
                    try:
                        stat=(path/'stat').read_text().rsplit(')',1)[1].split()
                        if int(stat[3])!=pid:continue
                        content=(path/'maps').read_text()
                        libs=sorted({line.split()[-1] for line in content.splitlines() if len(line.split())>=6 and any(t in line for t in ['libpangulu','libpangu_backend','libcudss'])})
                        if libs:seen[path.name]={'executable':str((path/'exe').resolve()),'libraries':libs}
                    except (OSError,ValueError,IndexError):continue
                if result.exists():
                    data=json.loads(result.read_text())
                    if data['status']!='completed':raise RuntimeError(str(data))
                    # Let the test bundle close its own window normally.
                    time.sleep(3);break
                time.sleep(.15)
            else:raise TimeoutError('GUI execution test timed out')
            sdk=str(core.sdk()/'lib/libpangulu.so')
            actual=[entry for entry in seen.values() if sdk in entry['libraries']]
            assert actual
            if mode=='direct':assert all(Path(entry['executable']).name=='comsollauncher' for entry in actual)
            else:assert all(Path(entry['executable']).name=='pangulu_worker' for entry in actual)
            reports.append({'mode':mode,'test_profile':str(home),'session':started['session'],'result':data,'maps':seen})
            print('GUI PASS',mode,data,flush=True)
        finally:
            try:os.killpg(pid,signal.SIGTERM)
            except ProcessLookupError:pass
    assert core.sha(reference)==before
    after={path:core.sha(path) for path in user_prefs};assert after==user_prefs
    (OUT/'gui-verification.json').write_text(json.dumps({'status':'completed','cases':reports,'real_user_preferences_unchanged':True},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
