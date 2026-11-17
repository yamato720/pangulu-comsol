"""Include only production backend sources and the configuration template."""
from pathlib import Path
import ast
import re
import shutil
from setuptools import setup
from setuptools.command.build_py import build_py

ROOT = Path(__file__).resolve().parent
VERSION = ast.literal_eval(re.search(r'__version__\s*=\s*("[^"]+")', (ROOT / 'src/python/pangulu_comsol/__init__.py').read_text())[1])


class BuildApplication(build_py):
    def find_package_modules(self, package, package_dir):
        modules = super().find_package_modules(package, package_dir)
        if '.dev' not in VERSION:
            modules = [entry for entry in modules if not (entry[0] == 'pangulu_comsol' and entry[1] == 'testing')]
        return modules

    def run(self):
        super().run()
        if '.dev' not in VERSION:
            (Path(self.build_lib) / 'pangulu_comsol/testing.py').unlink(missing_ok=True)
        resources = Path(self.build_lib) / "pangulu_comsol/resources"
        resources.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "config/distribution.json", resources / "distribution.json")
        for source in (ROOT / "src").rglob("*"):
            if source.is_file() and source.suffix in {".c", ".cpp", ".cu", ".h", ".java"}:
                target = resources / "src" / source.relative_to(ROOT / "src")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)


setup(cmdclass={"build_py": BuildApplication})
