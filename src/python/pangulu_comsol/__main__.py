"""Support python -m pangulu_comsol for the app's background commands."""
from .cli import entrypoint

raise SystemExit(entrypoint())
