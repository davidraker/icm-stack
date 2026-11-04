"""A fake platform environment for the tests: the real Python of the test venv (so cryptography, lib-web's user
manager and the poetry project script work), with pip calls swallowed, and shell doubles for volttron, vctl and
resource-config that record what they were asked and behave just enough (a listening web port, agent states)."""
import os
import stat
import sys
from pathlib import Path

VOLTTRON_PY = r'''
import configparser, os, socket, sys, time
home = os.environ['VOLTTRON_HOME']
cfg = configparser.ConfigParser(); cfg.read(os.path.join(home, 'config'))
open(os.path.join(home, 'fake_volttron.pid'), 'w').write(str(os.getpid()))
servers = []
if cfg.has_section('web'):
    port = int(cfg['web']['bind-web-address'].rsplit(':', 1)[1])
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(('127.0.0.1', port)); s.listen(5); servers.append(s)
open(os.path.join(home, 'volttron.log'), 'a').write('fake platform up\n')
while True:
    time.sleep(1)
'''

VCTL_PY = r'''
import os, sys, signal
home = os.environ['VOLTTRON_HOME']
state = os.path.join(home, 'fake_agents')
open(os.path.join(home, 'vctl.log'), 'a').write(' '.join(sys.argv[1:]) + '\n')
def alive():
    try:
        pid = int(open(os.path.join(home, 'fake_volttron.pid')).read()); os.kill(pid, 0); return True
    except Exception:
        return False
def agents():
    return [l.split() for l in open(state).read().splitlines()] if os.path.exists(state) else []
def save(rows):
    open(state, 'w').write(''.join(' '.join(r) + '\n' for r in rows))
args = sys.argv[1:]
if not alive():
    print('VOLTTRON is not running', file=sys.stderr); sys.exit(1)
if args[:1] == ['status']:
    print('UUID AGENT IDENTITY TAG PRIORITY STATUS HEALTH')
    for ident, tag, running in agents():
        print(f'1 fake-{tag}-0.1 {ident} {tag} 50 {"running [1]" if running == "1" else ""}')
elif args[:1] == ['install']:
    ident, tag = args[args.index('--vip-identity') + 1], args[args.index('--tag') + 1]
    rows = [r for r in agents() if r[0] != ident] + [[ident, tag, '1' if '--start' in args else '0']]; save(rows)
elif args[:1] in (['start'], ['stop']):
    tag = args[args.index('--tag') + 1]
    save([[i, t, '1' if (args[0] == 'start') else '0'] if t == tag else [i, t, r] for i, t, r in agents()])
elif args[:2] == ['shutdown', '--platform']:
    os.kill(int(open(os.path.join(home, 'fake_volttron.pid')).read()), signal.SIGTERM)
elif args[:1] == ['authz']:
    pass
elif args[:2] == ['config', 'store']:
    ident, name, path = args[2], args[3], args[4]
    dest = os.path.join(home, 'fake_store', ident, name.replace('/', '__')); os.makedirs(os.path.dirname(dest), exist_ok=True)
    open(dest, 'w').write(open(path).read())
elif args[:2] == ['config', 'list']:
    root = os.path.join(home, 'fake_store')
    d = os.path.join(root, args[2]) if len(args) > 2 else root
    for n in (sorted(os.listdir(d)) if os.path.isdir(d) else []):
        print(n.replace('__', '/'))
elif args[:2] == ['config', 'delete'] and '--all' in args:
    import shutil
    shutil.rmtree(os.path.join(home, 'fake_store', args[2]), ignore_errors=True)
elif args[:2] == ['config', 'get']:
    dest = os.path.join(home, 'fake_store', args[2], args[3].replace('/', '__'))
    if not os.path.exists(dest):
        print('no such configuration', file=sys.stderr); sys.exit(1)
    print(open(dest).read())
sys.exit(0)
'''

UI_PY = r'''
import socket, sys, time
port = int(sys.argv[sys.argv.index('--port') + 1])
open(sys.argv[0] + '.args', 'w').write(' '.join(sys.argv[1:]))
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(('127.0.0.1', port)); s.listen(5)
while True:
    time.sleep(1)
'''


def make(root: Path) -> Path:
    """Create the fake venv under ``root`` and return its path."""
    venv = root / 'venv'
    bin_dir = venv / 'bin'
    bin_dir.mkdir(parents=True)
    real = sys.executable
    (venv / 'volttron_fake.py').write_text(VOLTTRON_PY)
    (venv / 'vctl_fake.py').write_text(VCTL_PY)
    (venv / 'ui_fake.py').write_text(UI_PY)
    scripts = {
        # pip calls are recorded and succeed; everything else runs on the real interpreter
        # pip calls are recorded and succeed; lib-web's user manager (absent from the test venv) is doubled; the rest is real
        'python': (f'#!/bin/sh\nif [ "$1" = "-m" ] && [ "$2" = "pip" ]; then echo "$@" >> "{venv}/pip.log"; exit 0; fi\n'
                   f'case "$2" in *add_web_user*) printf \'{{"%s": {{"groups": ["admin", "vui"], "hashed_password": "fake"}}}}\' "$3" > "$VOLTTRON_HOME/web-users.json"; exit 0;; esac\n'
                   f'exec "{real}" "$@"\n'),
        'volttron': f'#!/bin/sh\nexec "{real}" "{venv}/volttron_fake.py" "$@"\n',
        'vctl': f'#!/bin/sh\nexec "{real}" "{venv}/vctl_fake.py" "$@"\n',
        'resource-config': f'#!/bin/sh\nexec "{real}" "{venv}/ui_fake.py" "$@"\n',
    }
    for name, text in scripts.items():
        path = bin_dir / name
        path.write_text(text)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return venv


def add_poetry(venv: Path) -> None:
    """A poetry double on the fake venv's bin, so the platform's start-up check passes without poetry installed."""
    path = venv / 'bin' / 'poetry'
    path.write_text('#!/bin/sh\necho "Poetry (version fake)"\n')
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
