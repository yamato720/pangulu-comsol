"""Keep mutable SDK/licence data outside an installed Python application."""
import os
from pathlib import Path


def source_project():
    candidate = Path(__file__).resolve().parents[3]
    if (candidate / "src/python/pangulu_comsol/paths.py").resolve() == Path(__file__).resolve():
        return candidate
    return None


def application_home():
    configured = os.environ.get("PANGU_COMSOL_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    project = source_project()
    if project is not None:
        return project
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return (base / "pangu-comsol").expanduser().resolve()


def production_sources():
    project = source_project()
    if project is not None:
        return project / "src"
    return Path(__file__).resolve().parent / "resources/src"


def configuration_template():
    project = source_project()
    if project is not None:
        return project / "config/distribution.json"
    return Path(__file__).resolve().parent / "resources/distribution.json"
