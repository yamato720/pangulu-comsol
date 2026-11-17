"""Build flavor; customer releases never enable optional test tools."""
from . import __version__


def is_development_build():
    return '.dev' in __version__
