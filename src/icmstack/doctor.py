"""What the machine has and lacks for the components, each finding with what to do about it."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass

from .processes import docker_available, docker_has_image, docker_has_volume, port_open

IMAGES = {'eclipse-mosquitto:2': 'broker', 'sep2server:e2e': 'sep2-server (build: ieee2030_5 driver tests/e2e_sep2server, Dockerfile.e2e)',
          'inverterclient:e2e': 'sep2-client (build from the GridAPPS-D client repository)',
          'ieee-std-1815-2-test-tool-backend-dev:latest': 'dnp3-outstation and dnp3-master (the test tool\'s backend image)'}
VOLUMES = {'ieee-std-1815-2-test-tool_cargo-cache': 'dnp3-outstation and dnp3-master (compiled reference binaries)'}
CHECKOUT_VARIABLES = [('ICM_MODULES', ('DER_CONTROL_MODULES',), 'the interoperability modules checkouts (agents from source)'),
                      ('VOLTTRON_MODULAR_DRIVERS', (), 'the modular driver tree (driver from source, PlatformCLI)'),
                      ('IEEE1815_2_TEST_TOOL', (), 'the IEEE 1815.2 test tool checkout (the DNP3 profile)')]
DEFAULT_PORTS = {8443: 'web service', 8600: 'UI', 1883: 'broker', 5020: 'sunspec-device', 20000: 'dnp3-outstation', 9443: 'sep2-server'}


@dataclass
class Finding:
    ok: bool
    what: str
    detail: str = ''
    fix: str = ''
    level: str = 'need'     # need | want | info

    def line(self) -> str:
        mark = 'ok ' if self.ok else ('!! ' if self.level == 'need' else '-- ')
        text = f'{mark}{self.what}'
        if self.detail:
            text += f': {self.detail}'
        if not self.ok and self.fix:
            text += f'\n      {self.fix}'
        return text


def checks(env=os.environ, probe_ports: bool = True) -> list[Finding]:
    out: list[Finding] = []
    out.append(Finding(sys.version_info >= (3, 10), 'Python', '.'.join(map(str, sys.version_info[:3])), 'Python 3.10 or later is required'))
    poetry = shutil.which('poetry')
    runs = subprocess.run(['poetry', '--version'], capture_output=True, text=True).returncode == 0 if poetry else False
    out.append(Finding(runs, 'poetry runs', (poetry or 'missing') + ('' if runs or not poetry else ' (found but does not run here: a pyenv shim outside a .python-version directory? icmstack pins PYENV_VERSION to the venv for the platform)'),
                       'the platform records its environment in a poetry project on first start: pipx install poetry', 'need'))
    docker = docker_available()
    out.append(Finding(docker, 'docker', 'reachable' if docker else 'not reachable', 'the broker and four devices run in containers; without docker name an existing broker', 'want'))
    if docker:
        for image, used_by in IMAGES.items():
            out.append(Finding(docker_has_image(image), f'image {image}', used_by, f'pull or build it ({used_by})', 'want'))
        for volume, used_by in VOLUMES.items():
            out.append(Finding(docker_has_volume(volume), f'volume {volume}', used_by, 'build the test tool once with its compose file', 'want'))
    for name, aliases, used_by in CHECKOUT_VARIABLES:
        value = next((env[v] for v in (name, *aliases) if env.get(v)), None)
        ok = bool(value) and os.path.isdir(os.path.expanduser(value))
        out.append(Finding(ok, name, value or 'not set', f'export {name}=<checkout dir> for {used_by}', 'want'))
    home = env.get('VOLTTRON_HOME')
    out.append(Finding(not home, 'VOLTTRON_HOME in this shell', home or 'not exported',
                       'icmstack sets VOLTTRON_HOME per profile; an exported one would make volttron and vctl read another installation', 'want'))
    if probe_ports:
        for port, used_by in DEFAULT_PORTS.items():
            busy = port_open('127.0.0.1', port, 0.2)
            out.append(Finding(not busy, f'port {port}', f'{used_by}: {"in use" if busy else "free"}', 'another answer will be asked for the port', 'info'))
    return out


def report(out=print, **kwargs) -> bool:
    findings = checks(**kwargs)
    for f in findings:
        out(f.line())
    needs = [f for f in findings if not f.ok and f.level == 'need']
    out(f'{len([f for f in findings if f.ok])} ok, {len(needs)} required missing, {len([f for f in findings if not f.ok]) - len(needs)} optional missing')
    return not needs
