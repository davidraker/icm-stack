"""Questions and how they are answered.

An answer given on the command line or in the environment settles a question and it is not asked. Otherwise the
question is asked, and the value in brackets is the profile's recorded answer when there is one, the built-in default
otherwise; Enter keeps it. ``--yes`` takes the bracketed value without asking. Secrets have no default, are never
recorded, and are read from a file option or an environment variable rather than a plain option."""
from __future__ import annotations

import getpass
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .profile import Profile

ENV_PREFIX = 'ICMSTACK_'


class PromptError(Exception):
    pass


@dataclass
class Question:
    key: str                                  # answers.toml key, e.g. instance_name
    text: str                                 # what the prompt says
    default: Any = None                       # value, or a callable returning one (evaluated when asked)
    kind: str = 'str'                         # str | int | float | bool | choice | path | secret
    choices: tuple = ()
    help: str = ''
    validate: Callable[[Any], str | None] | None = None    # returns a message when the value is not acceptable
    secret: bool = False
    extra_env: tuple = ()                     # other environment variables accepted, e.g. VOLTTRON_HOME

    @property
    def option(self) -> str:
        return '--' + self.key.replace('_', '-')

    @property
    def env(self) -> str:
        return ENV_PREFIX + self.key.upper()

    def coerce(self, raw: Any) -> Any:
        if raw is None or raw == '':
            return None
        if self.kind == 'int':
            return int(raw)
        if self.kind == 'float':
            return float(raw)
        if self.kind == 'bool':
            if isinstance(raw, bool):
                return raw
            text = str(raw).strip().lower()
            if text in ('y', 'yes', 'true', '1', 'on'):
                return True
            if text in ('n', 'no', 'false', '0', 'off'):
                return False
            raise ValueError(f'{raw!r} is not yes or no')
        if self.kind == 'path':
            return os.path.expanduser(str(raw))
        if self.kind == 'choice' and str(raw) not in [str(c) for c in self.choices]:
            raise ValueError(f'{raw!r} is not one of {", ".join(map(str, self.choices))}')
        return str(raw)


def shown(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'yes' if value else 'no'
    return str(value)


@dataclass
class Answers:
    """Resolves questions for one run."""
    profile: Profile
    cli: dict[str, Any] = field(default_factory=dict)     # option values the parser produced (None = not given)
    env: Mapping[str, str] = field(default_factory=lambda: os.environ)
    yes: bool = False
    input_fn: Callable[[str], str] = input
    getpass_fn: Callable[[str], str] = getpass.getpass
    out: Callable[[str], None] = print
    interactive: bool | None = None           # None: decide from stdin
    asked: set = field(default_factory=set)   # questions settled in this run: each is asked once, however many components need it
    _secrets: dict = field(default_factory=dict)   # secret answers for this run only, never written anywhere

    def _interactive(self) -> bool:
        return self.interactive if self.interactive is not None else sys.stdin.isatty()

    def settled(self, q: Question) -> tuple[bool, Any]:
        """(True, value) when the command line or the environment answers ``q``."""
        if self.cli.get(q.key) is not None:
            return True, q.coerce(self.cli[q.key])
        for name in (q.env, *q.extra_env):
            if self.env.get(name):
                return True, q.coerce(self.env[name])
        return False, None

    def offered(self, q: Question) -> Any:
        """What the prompt shows in brackets: the recorded answer, else the default."""
        if not q.secret and q.key in self.profile.answers:
            return self.profile.answers[q.key]
        return q.default() if callable(q.default) else q.default

    def ask(self, q: Question) -> Any:
        if q.key in self.asked and (q.secret or q.key in self.profile.answers):
            return self.profile.answers.get(q.key) if not q.secret else self._secrets[q.key]
        done, value = self.settled(q)
        if not done:
            value = self._prompt(q)
        self.asked.add(q.key)
        if q.secret:
            self._secrets[q.key] = value
        if q.validate:
            message = q.validate(value)
            if message:
                raise PromptError(f'{q.text}: {message}')
        if not q.secret and value is not None:
            self.profile.answers[q.key] = value
        return value

    def _prompt(self, q: Question) -> Any:
        offered = self.offered(q)
        if q.secret:
            if self.yes or not self._interactive():
                import secrets as _secrets
                value = _secrets.token_urlsafe(12)
                self.out(f'{q.text}: generated, shown once and not stored: {value}   (set {q.env} or pass {q.option}-file to choose it)')
                return value
            while True:
                first = self.getpass_fn(f'{q.text}: ')
                if not first:
                    self.out('a value is required')
                    continue
                if first == self.getpass_fn(f'{q.text} (again): '):
                    return first
                self.out('the two entries differ')
        if self.yes:
            return offered
        if not self._interactive():
            raise PromptError(f'{q.text}: no terminal to ask on; pass {q.option}, set {q.env}, or use --yes')
        while True:
            hint = f' ({"/".join(map(str, q.choices))})' if q.kind == 'choice' else (' (yes/no)' if q.kind == 'bool' else '')
            raw = self.input_fn(f'{q.text}{hint} [{shown(offered)}]{" (? for help)" if q.help else ""}: ').strip()
            if raw == '?' and q.help:
                self.out(q.help)
                continue
            if raw == '':
                if offered is None and q.kind != 'bool':
                    self.out('a value is required')
                    continue
                return offered
            try:
                value = q.coerce(raw)
            except ValueError as e:
                self.out(str(e))
                continue
            if q.validate and (message := q.validate(value)):
                self.out(message)
                continue
            return value

    def confirm(self, text: str, default: bool = True) -> bool:
        if self.yes or not self._interactive():
            return default
        while True:
            raw = self.input_fn(f'{text} [{"Y/n" if default else "y/N"}]: ').strip().lower()
            if raw == '':
                return default
            if raw in ('y', 'yes'):
                return True
            if raw in ('n', 'no'):
                return False

    def choose(self, text: str, options: list[tuple[str, str]], default: str | None = None) -> str | None:
        """A numbered menu; returns the chosen key, or None for an empty answer with no default."""
        if self.yes or not self._interactive():
            return default
        for i, (key, label) in enumerate(options, 1):
            self.out(f'  {i:2d}. {label}')
        while True:
            raw = self.input_fn(f'{text}{f" [{default}]" if default else ""}: ').strip()
            if raw == '':
                return default
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return options[int(raw) - 1][0]
            for key, _ in options:
                if raw == key:
                    return key
            self.out(f'choose 1 to {len(options)}')


def add_question_options(parser, questions: list[Question]) -> None:
    """One ``--key-name`` per question; secrets get ``--key-name-file`` instead."""
    seen = set()
    for q in questions:
        if q.key in seen:
            continue
        seen.add(q.key)
        if q.secret:
            parser.add_argument(q.option + '-file', dest=q.key + '_file', metavar='PATH', help=f'{q.help or q.text} (read from a file; or set {q.env})')
        elif q.kind == 'bool':
            import argparse
            parser.add_argument(q.option, dest=q.key, action=argparse.BooleanOptionalAction, default=None, help=f'{q.help or q.text} (or {q.env})')
        else:
            parser.add_argument(q.option, dest=q.key, default=None, metavar=q.kind.upper() if q.kind != 'choice' else '|'.join(map(str, q.choices)),
                                help=f'{q.help or q.text} (or {q.env})')


def cli_answers(namespace, questions: list[Question]) -> dict[str, Any]:
    """The option values the parser produced, with secret files read."""
    values: dict[str, Any] = {}
    for q in questions:
        if q.secret:
            path = getattr(namespace, q.key + '_file', None)
            if path:
                values[q.key] = open(os.path.expanduser(path), encoding='utf-8').read().rstrip('\n')
        elif getattr(namespace, q.key, None) is not None:
            values[q.key] = getattr(namespace, q.key)
    return values
