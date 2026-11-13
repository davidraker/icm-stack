"""The Python environment the platform and the agents run in, and the commands run inside it."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

PLATFORM_PACKAGES = ['volttron-core', 'volttron-lib-zmq', 'volttron-lib-auth', 'volttron-lib-web']
#: Distributions left out of the poetry project the platform locks on first start: git/path sources poetry cannot
#: reconcile, a pytest<8 pin, the RTC agent's classic-volttron pin. Agents import from the venv regardless.
POETRY_EXCLUDE = ('protocol-proxy-dnp3', 'dnp3py', 'volttron-lib-dnp3-driver', 'volttron-testing', 'volttron-realtime-control')
POETRY_LOCAL_CONFIG = '[virtualenvs]\ncreate = false\n\n[keyring]\nenabled = false\n'

# Runs inside the target environment: a pyproject pinning every installed distribution (editable ones as develop
# path dependencies), so the platform only locks it on first start instead of resolving and upgrading packages.
_PROJECT_SCRIPT = r'''
import json, sys
from importlib import metadata
exclude = set(sys.argv[1].split(',')) - {''}
pins, paths, seen = {}, {}, set()
for dist in metadata.distributions():
    name = dist.metadata['Name']
    if not name or name.lower() in ('volttron', *exclude):
        continue
    key = name.lower().replace('_', '-')
    info = json.loads(dist.read_text('direct_url.json') or '{}')
    if 'dir_info' in info:
        develop = 'true' if info['dir_info'].get('editable') else 'false'
        paths[key] = f'"{name}" = {{path = "{info["url"].removeprefix("file://")}", develop = {develop}}}'
        pins.pop(key, None)                 # a path install wins over a stale copy of the same name
    elif key not in paths and key not in pins:
        pins[key] = f'"{name}" = "{dist.version}"'
paths, pins = list(paths.values()), list(pins.values())
v = sys.version_info
print('\n'.join(['[tool.poetry]', 'name = "volttron"', 'version = "0.1.0"', 'description = ""',
    'authors = ["volttron <volttron@pnnl.gov>"]', 'package-mode = false', '',
    '[tool.poetry.dependencies]', f'python = "~{v.major}.{v.minor}.{v.micro}"', *sorted(paths), *sorted(pins), '',
    '[[tool.poetry.source]]', 'name = "test-pypi"', 'url = "https://test.pypi.org/simple/"', 'priority = "supplemental"', '',
    '[build-system]', 'requires = ["poetry-core>=2.0.0,<3.0.0"]', 'build-backend = "poetry.core.masonry.api"', '']))
'''

_EDITABLE_SCRIPT = r'''
import json
from importlib import metadata
found = {}
for dist in metadata.distributions():
    info = json.loads(dist.read_text('direct_url.json') or '{}')
    if info.get('dir_info', {}).get('editable') and dist.metadata['Name']:
        found[dist.metadata['Name'].lower().replace('_', '-')] = info['url'].removeprefix('file://')
print(json.dumps(found))
'''


def same_dependencies(project_a: str, project_b: str) -> bool:
    """Whether two poetry projects declare the same dependencies (names, pins, paths), whatever their formatting."""
    def deps(text):
        try:
            table = tomllib.loads(text).get('tool', {}).get('poetry', {}).get('dependencies', {})
        except tomllib.TOMLDecodeError:
            return None
        return {k.lower().replace('_', '-'): v for k, v in table.items()}
    a, b = deps(project_a), deps(project_b)
    return a is not None and a == b


def add_path_dependencies(pyproject: Path, projects: dict[str, str]) -> list[str]:
    """Record ``projects`` (name -> directory) as develop path dependencies of the poetry project unless they are
    already there as such; a plain version pin of the same name is replaced. Returns the names written."""
    lines = pyproject.read_text().splitlines()
    written, python_at = [], None
    for name, path in sorted(projects.items()):
        line = f'"{name}" = {{path = "{path}", develop = true}}'
        at = next((i for i, l in enumerate(lines) if re.match(rf'"{re.escape(name)}"\s*=', l, re.IGNORECASE)), None)
        if at is not None and lines[at] == line:
            continue
        if at is not None and 'path =' in lines[at]:
            continue                                # a path dependency already, from somewhere else: left alone
        if at is not None:
            lines[at] = line                        # a pinned release of the same name would shadow the checkout
        else:
            if python_at is None:
                python_at = next(i for i, l in enumerate(lines) if l.startswith('python = '))
            lines.insert(python_at + 1, line)
        written.append(name)
    if written:
        pyproject.write_text('\n'.join(lines) + '\n')
    return written


class EnvError(RuntimeError):
    pass


class PlatformEnv:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()

    @property
    def bin(self) -> Path:
        return self.path / ('Scripts' if os.name == 'nt' else 'bin')

    def exe(self, name: str) -> Path:
        return self.bin / name

    @property
    def python(self) -> Path:
        return self.exe('python')

    def exists(self) -> bool:
        return self.python.exists()

    def has(self, distribution: str) -> bool:
        if not self.exists():
            return False
        done = subprocess.run([str(self.python), '-c', f'import importlib.metadata as m; m.version({distribution!r})'], capture_output=True)
        return done.returncode == 0

    def has_exe(self, name: str) -> bool:
        return self.exe(name).exists()

    def create(self, out=print) -> None:
        out(f'creating {self.path}')
        subprocess.run([sys.executable, '-m', 'venv', str(self.path)], check=True)
        self.run(['python', '-m', 'pip', 'install', '-q', '--upgrade', 'pip'], timeout=600)

    def pip_install(self, specs: list[str], out=print, editable: bool = False, timeout: float = 1800) -> None:
        if not specs:
            return
        out(f'pip install {" ".join(specs)}')
        args = ['python', '-m', 'pip', 'install', '-q'] + (['-e'] if editable else []) + specs
        done = self.run(args, check=False, timeout=timeout)
        if done.returncode != 0:
            raise EnvError(f'pip install failed:\n{done.stdout[-2000:]}')

    def python_version(self) -> str | None:
        try:
            for line in (self.path / 'pyvenv.cfg').read_text().splitlines():
                key, _, value = line.partition('=')
                if key.strip() in ('version', 'version_info'):
                    return value.strip()
        except OSError:
            pass
        return None

    def process_env(self, extra: dict | None = None) -> dict:
        """The environment for commands of this venv: its bin first on PATH and, where pyenv shims are in use, the shim
        selection pinned to the venv's interpreter so that ``poetry`` (which the platform runs) resolves anywhere."""
        env = {**os.environ, **(extra or {})}
        env['PATH'] = f'{self.bin}{os.pathsep}{env.get("PATH", "")}'
        env.pop('VIRTUAL_ENV', None)
        env['VIRTUAL_ENV'] = str(self.path)
        poetry = shutil.which('poetry', path=env['PATH'])
        version = self.python_version()
        if poetry and '.pyenv' in poetry and 'shims' in poetry and version and 'PYENV_VERSION' not in os.environ:
            env['PYENV_VERSION'] = version
        return env

    def run(self, args: list[str], env: dict | None = None, check: bool = True, timeout: float = 300, cwd=None) -> subprocess.CompletedProcess:
        cmd = [str(self.exe(args[0])), *args[1:]]
        done = subprocess.run(cmd, env=self.process_env(env), capture_output=True, text=True, timeout=timeout, cwd=cwd)
        if check and done.returncode != 0:
            raise EnvError(f'{" ".join(args[:3])} failed ({done.returncode}):\n{(done.stdout + done.stderr)[-2000:]}')
        done.stdout = done.stdout + done.stderr if not check else done.stdout
        return done

    def poetry_works(self) -> str | None:
        """poetry's version when it runs in this venv's environment, else None."""
        try:
            done = subprocess.run(['poetry', '--version'], env=self.process_env(), capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return done.stdout.strip() if done.returncode == 0 else None

    def poetry_project(self, exclude=POETRY_EXCLUDE) -> str:
        return self.run(['python', '-c', _PROJECT_SCRIPT, ','.join(exclude)]).stdout

    def editable_projects(self) -> dict[str, str]:
        """The editable (path) installs of this environment: distribution name -> directory."""
        done = self.run(['python', '-c', _EDITABLE_SCRIPT])
        return json.loads(done.stdout or '{}')

    @staticmethod
    def running_venv() -> Path | None:
        """The venv this process runs in, when it is one."""
        if sys.prefix != getattr(sys, 'base_prefix', sys.prefix):
            return Path(sys.prefix)
        return None
