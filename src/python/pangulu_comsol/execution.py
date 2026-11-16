"""Session-scoped startup for the Pangu in-process execution mode."""
from pathlib import Path
import tempfile
from . import runtime as core


def direct_launcher():
    root=Path(core.cfg()['comsol_root'])
    source=root/'bin/comsol'
    original=source.read_text()
    needle='setflldpreload ${JREROOT}/lib/libjsig.so'
    if original.count(needle)!=1:
        raise ValueError('This COMSOL launcher is not recognized for direct Pangu startup; use worker execution')
    text=original.replace(needle,': # Pangu direct session: omit libjsig preloading')
    directory=core.ROOT/'runtime/launchers';directory.mkdir(parents=True,exist_ok=True)
    target=directory/'comsol-direct'
    if not target.is_file() or target.read_text()!=text:
        temporary=None
        try:
            with tempfile.NamedTemporaryFile(mode='w',dir=directory,prefix='.direct-',delete=False) as stream:
                temporary=Path(stream.name);stream.write(text)
            temporary.chmod(0o700);temporary.replace(target)
        finally:
            if temporary is not None:temporary.unlink(missing_ok=True)
    return target,{'vendor_launcher':str(source),'vendor_launcher_sha256':core.sha(source),
                   'session_launcher':str(target),'session_launcher_sha256':core.sha(target),
                   'libjsig_preload':'omitted_for_direct_execution','installation_modified':False}
