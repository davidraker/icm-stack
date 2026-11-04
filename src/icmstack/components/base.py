"""What every component provides, and the dependency order between components."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..prompts import Answers, Question
from ..profile import Profile


class NotReady(Exception):
    """The component exists in the design but this step of the implementation does not provide the action yet."""


@dataclass
class Status:
    installed: bool | None = None     # None: not applicable
    running: bool | None = None       # None: not a process (e.g. a configuration-only component)
    detail: str = ''

    @property
    def word(self) -> str:
        if self.running is True:
            return 'running'
        if self.installed is False:
            return 'not installed'
        if self.running is False:
            return 'stopped'
        return 'installed' if self.installed else '-'


@dataclass
class Context:
    profile: Profile
    answers: Answers
    out: Callable[[str], None] = print


class Component:
    name: str = ''
    group: str = 'framework'          # framework | device
    description: str = ''
    needs: tuple[str, ...] = ()       # must be installed and running first
    wants: tuple[str, ...] = ()       # started first when present, not required
    step: int = 1                     # the implementation step of the plan that provides it

    def questions(self) -> list[Question]:
        return []

    def install(self, ctx: Context) -> None:
        raise NotReady(f'{self.name}: install arrives with step {self.step} of the plan')

    def start(self, ctx: Context) -> None:
        raise NotReady(f'{self.name}: start arrives with step {self.step} of the plan')

    def stop(self, ctx: Context) -> None:
        raise NotReady(f'{self.name}: stop arrives with step {self.step} of the plan')

    def status(self, ctx: Context) -> Status:
        return Status(installed=ctx.profile.is_installed(self.name), running=None)

    def logs(self, ctx: Context, lines: int = 40) -> str:
        return ''

    def register(self, ctx: Context) -> None:
        raise NotReady(f'{self.name}: nothing to register')


class Registry:
    def __init__(self, components: list[Component]):
        self.components = {c.name: c for c in components}

    def __getitem__(self, name: str) -> Component:
        try:
            return self.components[name]
        except KeyError:
            raise KeyError(f'no component named {name!r}; known: {", ".join(self.components)}') from None

    def __contains__(self, name: str) -> bool:
        return name in self.components

    def names(self, group: str | None = None) -> list[str]:
        return [n for n, c in self.components.items() if group is None or c.group == group]

    def ordered(self, names: list[str], with_needs: bool = True) -> list[str]:
        """``names`` plus (when asked) what they need, dependencies before dependents, each once."""
        out: list[str] = []

        def visit(name: str, path: tuple = ()):
            if name in out:
                return
            if name in path:
                raise ValueError(f'dependency cycle: {" > ".join(path + (name,))}')
            component = self[name]
            if with_needs:
                for need in component.needs:
                    visit(need, path + (name,))
                for want in component.wants:
                    if want in names:
                        visit(want, path + (name,))
            out.append(name)

        for name in names:
            visit(name)
        return out

    def missing_needs(self, names: list[str]) -> list[str]:
        """What the ordered closure adds beyond ``names`` themselves."""
        return [n for n in self.ordered(names) if n not in names]

    def dependents(self, name: str) -> list[str]:
        return [n for n, c in self.components.items() if name in c.needs]
