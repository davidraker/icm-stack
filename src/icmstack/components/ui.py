"""The configuration UI (resource-config) pointed at the platform's web service."""
from __future__ import annotations

import secrets

from ..processes import ManagedProcess, port_open, wait_for
from ..prompts import Question
from .base import Component, Context, NotReady, Status
from .platform_modular import Web, env_of
from .agents import Q_SOURCE, checkout_root


class UI(Component):
    name = 'ui'
    description = 'configuration UI (resource-config)'
    needs = ('web',)
    step = 2

    Q_PORT = Question('ui_port', 'UI port', default=8600, kind='int')
    Q_BIND = Question('ui_bind', 'UI bind address', default='127.0.0.1', help='0.0.0.0 to reach it from other machines')
    Q_SRC = Question('source_ui', 'Where the UI is installed from', default='', help='a directory or a pip requirement; default: resource-config from PyPI, or the checkout under $ICM_MODULES/resource-config (the agent_source answer); not asked, --source-ui only')

    def questions(self):
        return [self.Q_PORT, self.Q_BIND, self.Q_SRC, Q_SOURCE]

    def install(self, ctx: Context) -> None:
        env = env_of(ctx)
        settled, explicit = ctx.answers.settled(self.Q_SRC)
        explicit = explicit if settled else ctx.profile.answers.get(self.Q_SRC.key)
        if explicit:
            from pathlib import Path
            path = Path(explicit).expanduser()
            env.pip_install([str(path)], ctx.out, editable=path.is_dir())
        elif ctx.answers.ask(Q_SOURCE) == 'checkout':
            root = checkout_root('ICM_MODULES')
            if root is None or not (root / 'resource-config').is_dir():
                raise NotReady('ui: set ICM_MODULES to a directory holding resource-config, or pass --source-ui')
            env.pip_install([str(root / 'resource-config')], ctx.out, editable=True)
        elif not env.has('resource-config'):
            env.pip_install(['resource-config'], ctx.out)
        ctx.answers.ask(self.Q_PORT)
        ctx.answers.ask(self.Q_BIND)
        ctx.profile.answers.setdefault('ui_secret_key', secrets.token_urlsafe(32))
        ctx.out('ui: installed')

    def process(self, ctx: Context) -> ManagedProcess:
        return ManagedProcess('ui', ctx.profile)

    def url(self, ctx: Context) -> str:
        a = ctx.profile.answers
        host = a.get('ui_bind', '127.0.0.1')
        return f'http://{"localhost" if host == "0.0.0.0" else host}:{a.get("ui_port", 8600)}/ui/'

    def start(self, ctx: Context) -> None:
        proc = self.process(ctx)
        if proc.running():
            ctx.out('ui: already running')
            return
        a = ctx.profile.answers
        web = Web()
        if not web.status(ctx).running:
            raise NotReady('the web service is not answering; start the platform first')
        env = {'RESOURCE_CONFIG_SECRET_KEY': a['ui_secret_key']}
        if a.get('web_cert'):
            env['RESOURCE_CONFIG_VERIFY_TLS'] = a['web_cert']
        proc.start([str(env_of(ctx).exe('resource-config')), '--platform-url', web.url(ctx), '--platform-name', a.get('instance_name', 'site1'),
                    '--host', str(a.get('ui_bind', '127.0.0.1')), '--port', str(a.get('ui_port', 8600))], env=env)
        port = int(a.get('ui_port', 8600))
        if not wait_for(lambda: port_open('127.0.0.1', port) or not proc.running(), 60):
            raise NotReady(f'the UI did not listen on {port}:\n{proc.tail(20)}')
        if not proc.running():
            raise NotReady(f'the UI exited:\n{proc.tail(30)}')
        ctx.out(f'ui: {self.url(ctx)}')

    def stop(self, ctx: Context) -> None:
        if self.process(ctx).stop():
            ctx.out('ui: stopped')

    def status(self, ctx: Context) -> Status:
        installed = ctx.profile.is_installed(self.name)
        return Status(installed, self.process(ctx).running() if installed else False, self.url(ctx) if installed else '')

    def logs(self, ctx: Context, lines: int = 40) -> str:
        return self.process(ctx).tail(lines)
