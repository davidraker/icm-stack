"""The icmstack command line: subcommands, and the menu when run bare."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__, doctor
from .components import Context, NotReady, Registry, default_registry
from .presets import PRESETS
from .profile import DEFAULT_ROOT, Profile
from .prompts import Answers, PromptError, add_question_options, cli_answers

ACTIONS = ('up', 'down', 'status', 'start', 'stop', 'restart', 'install', 'logs', 'register', 'config', 'doctor', 'shell')


class Cli:
    def __init__(self, registry: Registry | None = None, out=print, input_fn=input, interactive: bool | None = None):
        self.registry = registry or default_registry()
        self.out = out
        self.input_fn = input_fn
        self.interactive = interactive

    # ---------------------------------------------------------------- parser
    def all_questions(self):
        seen, questions = set(), []
        for component in self.registry.components.values():
            for q in component.questions():
                if q.key not in seen:
                    seen.add(q.key)
                    questions.append(q)
        return questions

    def parser(self) -> argparse.ArgumentParser:
        p = argparse.ArgumentParser(prog='icmstack', description=__doc__, allow_abbrev=False)    # so --web-password cannot mean --web-password-file
        p.add_argument('--version', action='version', version=f'icmstack {__version__}')
        p.add_argument('--profile', default=os.environ.get('ICMSTACK_PROFILE', 'default'), help='which recorded set of answers and which stack (default: default)')
        p.add_argument('--root', default=None, help=f'where profiles live (default {DEFAULT_ROOT}, or ICMSTACK_ROOT)')
        p.add_argument('-y', '--yes', action='store_true', help='ask nothing: take the recorded answer or the default for every open question')
        p.add_argument('--debug', action='store_true', help='show the traceback of an unexpected error')
        sub = p.add_subparsers(dest='action')
        answers = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
        add_question_options(answers, self.all_questions())
        s = sub.add_parser('up', parents=[answers], help='install and start a preset'); s.add_argument('preset', nargs='?', default='demo', choices=sorted(PRESETS))
        s.add_argument('--with', dest='with_', default='', help='comma-separated optional components to add (rtc, scheduler)')
        sub.add_parser('down', help='stop everything of the profile, reverse dependency order')
        sub.add_parser('status', help='one line per component')
        for name, text in (('start', 'start components, and what they need after asking'), ('stop', 'stop components'), ('restart', 'stop then start'), ('install', 'install only')):
            s = sub.add_parser(name, parents=[answers], help=text); s.add_argument('names', nargs='+')
        s = sub.add_parser('logs', help='the last lines of a component\'s log'); s.add_argument('name'); s.add_argument('-n', '--lines', type=int, default=40)
        s = sub.add_parser('register', parents=[answers], help='write a device\'s registry, device entry and resource into the platform')
        s.add_argument('name'); s.add_argument('--print', dest='print_only', action='store_true', help='only print the device\'s connection details')
        s = sub.add_parser('wire', parents=[answers], help='wire a showcase path (aliases, bridges, bus adapter entries) into the platform')
        s.add_argument('names', nargs='*', help='showcase names; --list shows them, --print NAME shows the UI settings to do it by hand')
        s.add_argument('--list', dest='list_', action='store_true'); s.add_argument('--print', dest='print_', metavar='NAME')
        s = sub.add_parser('control', parents=[answers], help='issue a control through a dummy party (sep2-server: a DERControl)')
        s.add_argument('name', choices=['sep2-server']); s.add_argument('--max-lim-w', type=int, required=True, help='active power limit in W')
        s = sub.add_parser('reset-config', parents=[answers], help='wipe the platform\'s configuration store (every agent\'s entries); asks first; devices keep running')
        s.add_argument('--identities', default='', help='comma-separated identities to wipe (default: every store the platform lists)')
        s = sub.add_parser('config', help='the recorded answers'); s.add_argument('--set', action='append', default=[], metavar='KEY=VALUE'); s.add_argument('--unset', action='append', default=[], metavar='KEY')
        s = sub.add_parser('doctor', help='what the machine has and lacks'); s.add_argument('--no-ports', action='store_true')
        sub.add_parser('shell', help='print the environment of the profile for eval in a shell')
        return p

    # ---------------------------------------------------------------- run
    def main(self, argv=None) -> int:
        args = self.parser().parse_args(argv)
        root = Path(args.root).expanduser() if args.root else None
        profile = Profile(args.profile, root)
        answers = Answers(profile, cli=cli_answers(args, self.all_questions()), yes=args.yes, input_fn=self.input_fn, out=self.out, interactive=self.interactive)
        for q in self.all_questions():          # an answer given on the command line or in the environment is recorded, asked or not
            settled, value = answers.settled(q)
            if settled and not q.secret:
                profile.answers[q.key] = value
        ctx = Context(profile, answers, self.out)
        try:
            if args.action is None:
                return self.menu(ctx)
            return getattr(self, 'do_' + args.action.replace('-', '_'))(ctx, args)
        except (PromptError, NotReady, KeyError, ValueError) as e:
            self.out(f'icmstack: {e}')
            return 2
        except KeyboardInterrupt:
            self.out('')
            return 130
        except Exception as e:    # anything unexpected: one line, the traceback on request
            if args.debug:
                raise
            self.out(f'icmstack: {type(e).__name__}: {e} (run again with --debug for the traceback)')
            return 3
        finally:
            if profile.answers or profile.dir.exists():
                profile.save()

    # ---------------------------------------------------------------- actions
    def do_status(self, ctx, args=None) -> int:
        rows = []
        for group in ('framework', 'device'):
            for name in self.registry.names(group):
                st = self.registry[name].status(ctx)
                rows.append((group, name, st.word, st.detail))
        width = max(len(r[1]) for r in rows)
        last = None
        for group, name, word, detail in rows:
            if group != last:
                self.out(f'{group}:')
                last = group
            self.out(f'  {name:<{width}}  {word:<14} {detail}')
        self.out(f'profile {ctx.profile.name} at {ctx.profile.dir}')
        return 0

    def do_doctor(self, ctx, args) -> int:
        return 0 if doctor.report(self.out, probe_ports=not args.no_ports) else 1

    def do_config(self, ctx, args) -> int:
        for item in args.set:
            key, _, value = item.partition('=')
            ctx.profile.answers[key.strip()] = value
        for key in args.unset:
            ctx.profile.answers.pop(key, None)
        if not ctx.profile.answers:
            self.out(f'no recorded answers in profile {ctx.profile.name}')
        for key, value in sorted(ctx.profile.answers.items()):
            self.out(f'{key} = {value}')
        return 0

    def do_wire(self, ctx, args) -> int:
        from .wiring import SHOWCASES, describe, wire
        if args.list_ or not (args.names or args.print_):
            for s in SHOWCASES.values():
                self.out(f'{s.name:<24} {s.kind:<9} {s.title}  [{", ".join(s.needs)}]')
            return 0
        if args.print_:
            self.out(describe(ctx, args.print_)); return 0
        unknown = [n for n in args.names if n not in SHOWCASES]
        if unknown:
            self.out(f'unknown showcase(s): {", ".join(unknown)}; see icmstack wire --list'); return 2
        wire(ctx, args.names)
        return 0

    def do_control(self, ctx, args) -> int:
        from .components.devices.containers import Sep2Server
        mrid = Sep2Server().create_control(ctx, args.max_lim_w)
        self.out(f'sep2-server: DERControl {mrid} created, opModMaxLimW {args.max_lim_w} W for 120 s')
        return 0

    def do_reset_config(self, ctx, args) -> int:
        wanted = [i.strip() for i in args.identities.split(',') if i.strip()] if args.identities else None
        self.reset_stores(ctx, wanted, default=False, label='reset-config')
        return 0

    def reset_stores(self, ctx, identities: list[str] | None = None, default: bool = False, label: str = 'reset-config', reason: str = '') -> bool:
        """Empty the agents' configuration stores (all, or the given identities) after asking. With the platform stopped
        the store is one <identity>.store file per agent under the home, deleted directly; running, vctl does it.
        Returns whether anything was deleted."""
        from .components.platform_modular import Platform, home_of, vctl
        if not Platform().process(ctx).running():
            store = home_of(ctx) / 'configuration_store'
            files = sorted(store.glob('*.store')) if store.is_dir() else []
            files = [f for f in files if identities is None or f.stem in identities]
            if not files:
                if label == 'reset-config':
                    self.out(f'{label}: the platform is not running and {store} holds no store files')
                return False
            if not ctx.answers.confirm(f'{reason}The platform is stopped. Delete the store files of {", ".join(f.stem for f in files)} from {store}?', default=default):
                self.out(f'{label}: nothing deleted'); return False
            for f in files:
                f.unlink(); self.out(f'{f.stem}: store file deleted')
            return True
        if identities is None:
            listing = vctl(ctx, 'config', 'list', check=False, timeout=60)
            identities = [l.strip() for l in listing.stdout.splitlines() if l.strip() and not l.startswith('Failed')]
        if not identities:
            if label == 'reset-config':
                self.out(f'{label}: no configuration store has entries')
            return False
        if not ctx.answers.confirm(f'{reason}Delete every configuration entry of {", ".join(identities)}?', default=default):
            self.out(f'{label}: nothing deleted'); return False
        for identity in identities:
            done = vctl(ctx, 'config', 'delete', identity, '--all', check=False, timeout=120)
            self.out(f'{identity}: {"store wiped" if done.returncode == 0 else "failed: " + done.stdout.strip()[-200:]}')
        return True

    def do_shell(self, ctx, args) -> int:
        self.out(f'export VOLTTRON_HOME={ctx.profile.volttron_home}')
        self.out(f'export ICMSTACK_PROFILE={ctx.profile.name}')
        if ctx.profile.answers.get('venv'):
            self.out(f'. {ctx.profile.answers["venv"]}/bin/activate')
        return 0

    def do_logs(self, ctx, args) -> int:
        text = self.registry[args.name].logs(ctx, args.lines)
        self.out(text.rstrip('\n') if text else f'{args.name}: no log')
        return 0

    def _run(self, ctx, names: list[str], action: str, with_needs: bool = True) -> int:
        order = self.registry.ordered(names) if with_needs else names
        extra = [n for n in order if n not in names]
        if extra and action in ('start', 'install'):
            if not ctx.answers.confirm(f'{", ".join(names)} need{"s" if len(names) == 1 else ""} {", ".join(extra)}; {action} those too?'):
                order = [n for n in order if n in names]
        if action == 'stop':
            order = list(reversed(self.registry.ordered(names)))
            order = [n for n in order if n in names]
            for name in names:
                dependents = [d for d in self.registry.dependents(name) if self.registry[d].status(ctx).running]
                if dependents and not ctx.answers.confirm(f'{", ".join(dependents)} depend on {name}; stop {name} anyway?', default=False):
                    order.remove(name)
        failures, failed = 0, set()
        # Installs of every component first, then the starts: the web section is then in the platform's config before
        # its first start, and a failed install blocks the starts that would have needed it.
        phases = [('install', order), ('start', order)] if action == 'start' else [(action, order)]
        for phase, names_in_phase in phases:
            for name in names_in_phase:
                component = self.registry[name]
                blocked = [n for n in self.registry.ordered([name]) if n in failed and n != name]
                if blocked and phase in ('start', 'install'):
                    if phase == 'start' or name not in failed:
                        self.out(f'{name}: skipped, {", ".join(blocked)} did not {phase}')
                    failed.add(name); failures += 1
                    continue
                if name in failed:
                    continue
                try:
                    # "icmstack install NAME" always installs (that is how a component is re-installed after a source
                    # change); the install phase of start only fills in what is missing.
                    if phase == 'install' and (action == 'install' or not ctx.profile.is_installed(name)):
                        component.install(ctx)
                        ctx.profile.mark_installed(name)
                    elif phase == 'start':
                        component.start(ctx); ctx.profile.mark_started(name)
                    elif phase == 'stop':
                        component.stop(ctx); ctx.profile.mark_started(name, False)
                except NotReady as e:
                    self.out(f'{name}: {e}')
                    failures += 1; failed.add(name)
                except Exception as e:    # one component's failure does not stop the others
                    self.out(f'{name}: {phase} failed: {e}')
                    failures += 1; failed.add(name)
        return 1 if failures else 0

    def do_start(self, ctx, args) -> int:
        return self._run(ctx, args.names, 'start')

    def do_install(self, ctx, args) -> int:
        return self._run(ctx, args.names, 'install')

    def do_stop(self, ctx, args) -> int:
        return self._run(ctx, args.names, 'stop')

    def do_restart(self, ctx, args) -> int:
        self._run(ctx, args.names, 'stop')
        return self._run(ctx, args.names, 'start')

    def do_up(self, ctx, args) -> int:
        preset = PRESETS[args.preset]
        names = list(preset['components'])
        for optional in preset.get('optional', []):
            if optional in (args.with_ or '').split(',') or ctx.answers.confirm(f'include {optional} ({self.registry[optional].description})?', default=False):
                names.append(optional)
        ctx.profile.answers['preset'] = args.preset
        self.out(f'{args.preset}: {preset["description"]}')
        if preset.get('reset'):
            # "Nothing configured" includes what earlier runs of this profile left in the agents' stores.
            self.reset_stores(ctx, default=True, label=args.preset, reason=f'{args.preset} starts from empty configuration stores. ')
        # Parties that talk to a device the platform serves start after registration has written that served device.
        late = [n for n in names if getattr(self.registry[n], 'after_register', False)]
        code = self._run(ctx, [n for n in names if n not in late], 'start')
        if preset.get('register'):
            for name in names:
                if self.registry[name].group == 'device' and name != 'fake-device':    # the fake device registers when started
                    try:
                        if not ctx.profile.is_installed(name):
                            self.registry[name].install(ctx); ctx.profile.mark_installed(name)
                        self.registry[name].register(ctx)
                    except NotReady as e:
                        self.out(f'{name}: {e}'); code = 1
            if late:
                code = self._run(ctx, late, 'start') or code
        if preset.get('wire'):
            from .wiring import SHOWCASES, wire
            wanted = list(SHOWCASES) if preset['wire'] == 'all' else list(preset['wire'])
            for showcase in wanted:          # each on its own, so one missing device does not stop the others
                try:
                    wire(ctx, [showcase])
                except NotReady as e:
                    self.out(f'wiring: {showcase}: {e}'); code = 1
        return code

    def do_down(self, ctx, args) -> int:
        started = [n for n in ctx.profile.state['started'] if n in self.registry]
        if not started:
            # Nothing recorded (a deleted profile, a crashed run): still stop what carries this profile's name or home.
            import subprocess
            names = subprocess.run(['docker', 'ps', '-aq', '--filter', f'name=^icmstack-{ctx.profile.name}-'], capture_output=True, text=True).stdout.split()
            if names:
                subprocess.run(['docker', 'rm', '-f', *names], capture_output=True)
                self.out(f'stopped {len(names)} container(s) of profile {ctx.profile.name}')
            from .components.platform_modular import Platform
            platform = Platform()
            if platform.strays(ctx) or platform.leftovers(ctx) or platform.process(ctx).running():
                platform.stop(ctx)
            elif not names:
                self.out('nothing is recorded as started, and nothing of this profile is running')
            return 0
        order = [n for n in reversed(self.registry.ordered(started)) if n in started]
        code = 0
        for name in order:
            try:
                self.registry[name].stop(ctx); ctx.profile.mark_started(name, False)
            except Exception as e:
                self.out(f'{name}: stop failed: {e}'); code = 1
        return code

    def do_register(self, ctx, args) -> int:
        component = self.registry[args.name]
        if args.print_only:
            details = getattr(component, 'connection_details', None)
            self.out(details(ctx) if details else f'{args.name}: no connection details')
            return 0
        component.register(ctx)
        return 0

    # ---------------------------------------------------------------- menu
    def menu(self, ctx) -> int:
        while True:
            self.out('')
            self.do_status(ctx)
            self.out('')
            options = [('up', 'install and start a preset'), ('start', 'start a component'), ('stop', 'stop a component'),
                       ('down', 'stop everything'), ('doctor', 'check this machine'), ('config', 'show the recorded answers'), ('quit', 'leave')]
            choice = ctx.answers.choose('What now?', options, default='quit')
            if choice in (None, 'quit'):
                return 0
            if choice == 'up':
                preset = ctx.answers.choose('Preset', [(k, f'{k}: {v["description"]}') for k, v in PRESETS.items()], default='showroom')
                if preset:
                    self.do_up(ctx, argparse.Namespace(preset=preset, with_=''))
            elif choice in ('start', 'stop'):
                names = self.registry.names()
                name = ctx.answers.choose('Component', [(n, f'{n:<18} {self.registry[n].description}') for n in names])
                if name:
                    self._run(ctx, [name], choice)
            elif choice == 'down':
                self.do_down(ctx, None)
            elif choice == 'doctor':
                doctor.report(self.out)
            elif choice == 'config':
                self.do_config(ctx, argparse.Namespace(set=[], unset=[]))


def main(argv=None) -> int:
    return Cli().main(argv)


if __name__ == '__main__':
    sys.exit(main())
