"""Include only production backend sources and the configuration template."""
from pathlib import Path
import shutil
from setuptools import setup
from setuptools.command.build_py import build_py

ROOT = Path(__file__).resolve().parent


class BuildApplication(build_py):
    def run(self):
        super().run()
        resources = Path(self.build_lib) / "pangulu_comsol/resources"
        resources.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "config/distribution.json", resources / "distribution.json")
        for source in (ROOT / "src").rglob("*"):
            if source.is_file() and source.suffix in {".c", ".cpp", ".cu", ".h", ".java"}:
                target = resources / "src" / source.relative_to(ROOT / "src")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)


setup(cmdclass={"build_py": BuildApplication})
