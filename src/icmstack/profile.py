"""A profile: the recorded answers, the state of what is installed and started, and the directory layout."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

DEFAULT_ROOT = Path(os.environ.get('ICMSTACK_ROOT', '~/.icmstack')).expanduser()


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return '[' + ', '.join(_toml_value(v) for v in value) + ']'
    return json.dumps(str(value))    # a TOML basic string is a JSON string for the characters we write


def dump_toml(table: dict[str, Any]) -> str:
    """The subset of TOML the answers need: one flat table of strings, numbers, booleans and lists."""
    return ''.join(f'{key} = {_toml_value(value)}\n' for key, value in sorted(table.items()) if value is not None)


class Profile:
    def __init__(self, name: str = 'default', root: Path | None = None):
        self.name = name
        self.dir = (root or DEFAULT_ROOT) / name
        self.answers: dict[str, Any] = {}
        self.state: dict[str, Any] = {'installed': [], 'started': []}
        self.load()

    # ---------------------------------------------------------------- files
    @property
    def answers_file(self) -> Path:
        return self.dir / 'answers.toml'

    @property
    def state_file(self) -> Path:
        return self.dir / 'state.json'

    @property
    def logs(self) -> Path:
        return self.dir / 'logs'

    @property
    def pids(self) -> Path:
        return self.dir / 'pids'

    @property
    def volttron_home(self) -> Path:
        return Path(self.answers.get('volttron_home') or self.dir / 'volttron_home').expanduser()

    def ensure(self) -> None:
        for d in (self.dir, self.logs, self.pids):
            d.mkdir(parents=True, exist_ok=True)
        try:
            self.dir.chmod(0o700)
        except OSError:
            pass

    def load(self) -> None:
        if self.answers_file.exists():
            self.answers = tomllib.loads(self.answers_file.read_text())
        if self.state_file.exists():
            self.state = json.loads(self.state_file.read_text())
            self.state.setdefault('installed', [])
            self.state.setdefault('started', [])

    def save(self) -> None:
        self.ensure()
        self.answers_file.write_text(dump_toml(self.answers))
        self.answers_file.chmod(0o600)
        self.state_file.write_text(json.dumps(self.state, indent=1))

    # ---------------------------------------------------------------- state
    def mark_installed(self, name: str, installed: bool = True) -> None:
        items = self.state['installed']
        if installed and name not in items:
            items.append(name)
        if not installed and name in items:
            items.remove(name)
        self.save()

    def mark_started(self, name: str, started: bool = True) -> None:
        items = self.state['started']
        if started and name not in items:
            items.append(name)
        if not started and name in items:
            items.remove(name)
        self.save()

    def is_installed(self, name: str) -> bool:
        return name in self.state['installed']

    @classmethod
    def existing(cls, root: Path | None = None) -> list[str]:
        base = root or DEFAULT_ROOT
        return sorted(p.name for p in base.iterdir() if (p / 'answers.toml').exists()) if base.exists() else []
