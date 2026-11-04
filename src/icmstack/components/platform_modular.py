"""The platform on modular VOLTTRON (volttron-core, ZMQ, auth on) and its web service, run from a Python environment."""
from __future__ import annotations

import re
import secrets
import time
from pathlib import Path

from ..processes import ManagedProcess, port_open, processes_with_env, stop_pid, wait_for
from ..prompts import Question
from ..venv import PLATFORM_PACKAGES, POETRY_LOCAL_CONFIG, PlatformEnv
from .base import Component, Context, NotReady, Status

RPC_ALLOW_LIST = [
    'platform.presentation: resolve, list_formats, list_transforms, describe_transform, chain_report, compile_pattern, edge_report, preview, check_references, discover',
    'platform.resource_bridge: statistics',
]


def default_venv(ctx: Context) -> str:
    return venv_choice(ctx)[0]


def venv_choice(ctx: Context) -> tuple[str, str]:
    """(default venv, why): the virtual environment icmstack runs in, whatever it holds (the platform packages are
    installed into it when missing); a new venv under the profile only when icmstack runs outside any venv, such as a
    system or pyenv interpreter, which should not receive the platform packages unasked."""
    import sys
    running = PlatformEnv.running_venv()
    if running is None:
        return str(ctx.profile.dir / 'venv'), f'icmstack runs in {sys.prefix}, which is not a virtual environment, so a new venv is proposed'
    return str(running), f'the venv icmstack runs in'


class PlatformQuestions:
    @staticmethod
    def venv(ctx):
        default, why = venv_choice(ctx)
        return Question('venv', f'Python environment (venv) for the platform and agents ({why})', default=default, kind='path',
                        help='an existing venv is reused and gets the platform packages when it lacks them; a missing path is created')

    @staticmethod
    def home(ctx):
        return Question('volttron_home', 'VOLTTRON_HOME, the platform\'s own directory', default=lambda: str(ctx.profile.dir / 'volttron_home'), kind='path')

    instance = Question('instance_name', 'Instance name (also the platform name the UI shows)', default='site1')
    backend = Question('backend', 'Platform backend', default='modular', kind='choice', choices=('modular', 'fastlib'))


def env_of(ctx: Context) -> PlatformEnv:
    return PlatformEnv(ctx.profile.answers.get('venv') or default_venv(ctx))


def home_of(ctx: Context) -> Path:
    return ctx.profile.volttron_home


def vctl(ctx: Context, *args: str, check: bool = True, timeout: float = 300):
    return env_of(ctx).run(['vctl', *args], env={'VOLTTRON_HOME': str(home_of(ctx))}, check=check, timeout=timeout)


def write_config(ctx: Context) -> Path:
    """``$VOLTTRON_HOME/config`` from the recorded answers: the platform section, and the web section once web is installed."""
    a = ctx.profile.answers
    home = home_of(ctx)
    home.mkdir(parents=True, exist_ok=True)
    lines = ['[volttron]', f'instance-name = {a.get("instance_name", "site1")}', 'messagebus = zmq', 'auth-enabled = True',
             'agent-isolation-mode = False', '']
    if ctx.profile.is_installed('web') and a.get('bind'):
        lines += ['[web]', f'bind-web-address = {a["bind"]}']
        if a['bind'].startswith('https'):
            lines += [f'web-ssl-cert = {a["web_cert"]}', f'web-ssl-key = {a["web_key"]}']
        else:
            lines += [f'web-secret-key = {a.get("web_secret_key") or secrets.token_hex(32)}']
        lines += ['rpc-allow-list =', *(f'    {entry}' for entry in RPC_ALLOW_LIST), '']
    path = home / 'config'
    path.write_text('\n'.join(lines))
    return path


class Platform(Component):
    name = 'platform'
    description = 'VOLTTRON platform (modular; fastlib later)'
    step = 2

    def questions(self):
        # Option generation needs context-free questions; install asks the same keys with profile-dependent defaults.
        return [PlatformQuestions.backend,
                Question('venv', 'Python environment (venv) for the platform and agents; one with volttron-core is reused, a missing one is created', default='', kind='path'),
                Question('volttron_home', 'VOLTTRON_HOME, the platform\'s own directory', default='', kind='path'),
                PlatformQuestions.instance]

    def install(self, ctx: Context) -> None:
        if ctx.answers.ask(PlatformQuestions.backend) != 'modular':
            raise NotReady('backend fastlib: not available yet. The upstream der-control-fastlib docker stack has the server and the '
                           'interoperability-service containers but no Resource Bridge or bus adapter containers, which the presets '
                           'need; the backend is added when those exist (plan step 5). Use --backend modular.')
        env = PlatformEnv(ctx.answers.ask(PlatformQuestions.venv(ctx)))
        if not env.exists():
            env.create(ctx.out)
        if not env.has('volttron-core'):
            env.pip_install(PLATFORM_PACKAGES, ctx.out)
        ctx.answers.ask(PlatformQuestions.home(ctx))
        ctx.answers.ask(PlatformQuestions.instance)
        write_config(ctx)
        ctx.out(f'platform: {home_of(ctx)} prepared for instance {ctx.profile.answers["instance_name"]}')

    def process(self, ctx: Context) -> ManagedProcess:
        return ManagedProcess('platform', ctx.profile)

    def strays(self, ctx: Context) -> list[tuple[int, str]]:
        """Platform processes bound to this home that no pidfile of ours tracks."""
        own = self.process(ctx).pid()
        return [(pid, cmd) for pid, cmd in processes_with_env('VOLTTRON_HOME', str(home_of(ctx)), 'volttron') if pid != own and ' -l ' in cmd]

    def start(self, ctx: Context, timeout: float = 900.0) -> None:
        proc = self.process(ctx)
        if proc.running():
            ctx.out('platform: already running')
            return
        env, home = env_of(ctx), home_of(ctx)
        strays = self.strays(ctx)
        if strays:
            # Two platforms on one home cannot both bind the IPC socket; the second hangs after a bind error.
            listing = '; '.join(f'pid {pid}: {cmd[:90]}' for pid, cmd in strays)
            if not ctx.answers.confirm(f'platform: another platform already uses {home} ({listing}). Stop it first?', default=True):
                raise NotReady(f'another platform uses {home}; stop it (kill {" ".join(str(p) for p, _ in strays)}) or choose another VOLTTRON_HOME')
            for pid, _ in strays:
                ctx.out(f'platform: stopping pid {pid}' + ('' if stop_pid(pid) else ' failed'))
        write_config(ctx)
        if not (home / 'pyproject.toml').exists():
            # The first start records the environment in a poetry project; seeding it means the platform only locks it.
            (home / 'poetry.toml').write_text(POETRY_LOCAL_CONFIG)
            (home / 'pyproject.toml').write_text(env.poetry_project())
            (home / 'poetry.lock').unlink(missing_ok=True)
            ctx.out('platform: first start in this home, the platform records its environment (a minute or two)')
        if not env.poetry_works():
            raise NotReady('poetry does not run in the platform environment (the platform needs it on first start to record '
                           'its environment); install it (pipx install poetry) or fix its shim')
        # -v (INFO): -vv logs every inotify event of the home's file watchers, tens of millions of lines a day.
        proc.start([str(env.exe('volttron')), '-v', '-l', str(home / 'volttron.log')], env=env.process_env({'VOLTTRON_HOME': str(home)}), cwd=home)
        started = time.time()
        while time.time() - started < timeout:
            if not proc.running():
                errors = [l for l in self.logs(ctx, 200).splitlines() if ' ERROR' in l or 'Traceback' in l or 'Error' in l]
                raise NotReady('platform exited:\n' + '\n'.join((errors or self.logs(ctx, 15).splitlines())[-15:]) + f'\n(full log: {home / "volttron.log"})')
            crashed = [l for l in proc.tail(60).splitlines() if 'Error' in l and 'Traceback' not in l and 'UserWarning' not in l]
            if any('Address already in use' in l or 'ZMQError' in l for l in crashed):
                # The router died on its socket but the process lingers: waiting would never end.
                proc.stop()
                raise NotReady('platform: the message bus could not bind its socket (another platform on this home?):\n' + '\n'.join(crashed[-3:]))
            if vctl(ctx, 'status', check=False, timeout=30).returncode == 0:
                ctx.out(f'platform: running, instance {ctx.profile.answers.get("instance_name")} in {home}')
                return
            time.sleep(3)
            if int(time.time() - started) % 30 < 3:
                ctx.out(f'  waiting for the platform ({int(time.time() - started)} s)')
        raise NotReady(f'platform did not answer within {timeout:.0f} s; log: {home / "volttron.log"}')

    def stop(self, ctx: Context) -> None:
        proc = self.process(ctx)
        strays = self.strays(ctx)
        if not proc.running() and not strays:
            ctx.out('platform: not running')
            return
        vctl(ctx, 'shutdown', '--platform', check=False, timeout=60)
        wait_for(lambda: not proc.running() and not self.strays(ctx), 30)
        proc.stop()
        for pid, _ in self.strays(ctx):
            ctx.out(f'platform: stopping stray pid {pid}' + ('' if stop_pid(pid) else ' failed'))
        ctx.out('platform: stopped')

    def status(self, ctx: Context) -> Status:
        installed = ctx.profile.is_installed(self.name)
        running = self.process(ctx).running()
        strays = self.strays(ctx) if (installed or not running) else []
        detail = f'{ctx.profile.answers.get("instance_name", "")} · {home_of(ctx)}' if installed else ''
        if strays:
            detail += f' · untracked platform process on this home: pid {", ".join(str(p) for p, _ in strays)}'
        return Status(installed, running or bool(strays), detail)

    def logs(self, ctx: Context, lines: int = 40) -> str:
        log = home_of(ctx) / 'volttron.log'
        return ''.join(log.read_text(errors='replace').splitlines(keepends=True)[-lines:]) if log.exists() else ''


class Web(Component):
    name = 'web'
    description = 'web service: certificate, admin user, authz role'
    needs = ('platform',)
    step = 2

    Q_BIND = Question('bind', 'Web bind address', default='https://0.0.0.0:8443')
    Q_CERT = Question('web_cert', 'TLS certificate (PEM), empty to generate a self-signed one', default='', kind='path')
    Q_KEY = Question('web_key', 'TLS private key (PEM, RSA)', default='', kind='path')
    Q_USER = Question('web_user', 'Web admin user', default='admin')
    Q_PASSWORD = Question('web_password', 'Web admin password', kind='secret', secret=True)

    def questions(self):
        return [self.Q_BIND, self.Q_CERT, self.Q_KEY, self.Q_USER, self.Q_PASSWORD]

    def install(self, ctx: Context) -> None:
        a = ctx.answers
        bind = a.ask(self.Q_BIND)
        if bind.startswith('https'):
            cert = a.ask(self.Q_CERT)
            if cert:
                a.ask(self.Q_KEY)
            else:
                self.generate_certificate(ctx)
        else:
            ctx.profile.answers.setdefault('web_secret_key', secrets.token_hex(32))
        user, password = a.ask(self.Q_USER), a.ask(self.Q_PASSWORD)
        home = home_of(ctx)
        home.mkdir(parents=True, exist_ok=True)
        env = env_of(ctx)
        if not env.has('volttron-lib-web'):
            env.pip_install(['volttron-lib-web'], ctx.out)
        # web-users.json is a file in VOLTTRON_HOME; lib-web's helper writes it, no running platform needed.
        env_of(ctx).run(['python', '-c', 'import sys; from volttron.services.web.user_manager import add_web_user; '
                         'add_web_user(sys.argv[1], sys.argv[2], groups=["admin", "vui"], overwrite=True)', user, password],
                        env={'VOLTTRON_HOME': str(home)})
        ctx.profile.mark_installed(self.name)
        write_config(ctx)
        platform = Platform()
        if platform.process(ctx).running():
            ctx.out('web: the platform re-reads its config on start; restarting it')
            platform.stop(ctx)
            platform.start(ctx)
        ctx.out(f'web: {bind}, admin user {user}')

    def generate_certificate(self, ctx: Context) -> None:
        certs = ctx.profile.dir / 'certs'
        certs.mkdir(parents=True, exist_ok=True)
        import socket
        names = ['localhost', socket.gethostname()]
        script = '''
import datetime, ipaddress, sys
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
out, names = sys.argv[1], sys.argv[2].split(',')
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)      # RSA: the web service signs its tokens RS256
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])])
now = datetime.datetime.now(datetime.timezone.utc)
san = [x509.DNSName(n) for n in names] + [x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]
cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(san), critical=False).sign(key, hashes.SHA256()))
open(out + '/web.key', 'wb').write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
open(out + '/web.crt', 'wb').write(cert.public_bytes(serialization.Encoding.PEM))
'''
        env_of(ctx).run(['python', '-c', script, str(certs), ','.join(names)])
        (certs / 'web.key').chmod(0o600)
        ctx.profile.answers['web_cert'], ctx.profile.answers['web_key'] = str(certs / 'web.crt'), str(certs / 'web.key')
        ctx.out(f'web: self-signed RSA certificate for {", ".join(names)} in {certs}')

    def url(self, ctx: Context) -> str:
        bind = ctx.profile.answers.get('bind', 'https://0.0.0.0:8443')
        m = re.match(r'(https?)://([^:/]+):?(\d+)?', bind)
        scheme, host, port = m.group(1), m.group(2), m.group(3) or ('443' if m.group(1) == 'https' else '80')
        return f'{scheme}://{"localhost" if host in ("0.0.0.0", "") else host}:{port}'

    def _port(self, ctx) -> int:
        return int(self.url(ctx).rsplit(':', 1)[1])

    def start(self, ctx: Context) -> None:
        if not Platform().process(ctx).running():
            raise NotReady('the platform is not running')
        if not wait_for(lambda: port_open('127.0.0.1', self._port(ctx)), 60):
            raise NotReady(f'the web service did not listen on {self.url(ctx)}; see the platform log')
        # Without this role the web service may start and stop agents but every configuration save returns 403.
        done = vctl(ctx, 'authz', 'add', 'agent', 'platform.web', '--role-names', 'default_rpc_capabilities', check=False)
        if done.returncode != 0 and 'already' not in done.stdout.lower():
            ctx.out(f'web: authz role not added: {done.stdout.strip()[-300:]}')
        ctx.out(f'web: {self.url(ctx)}')

    def stop(self, ctx: Context) -> None:
        ctx.out('web: part of the platform process; stop the platform to stop it')

    def status(self, ctx: Context) -> Status:
        installed = ctx.profile.is_installed(self.name)
        return Status(installed, port_open('127.0.0.1', self._port(ctx)) if installed else False, self.url(ctx) if installed else '')
