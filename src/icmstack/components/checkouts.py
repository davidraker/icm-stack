"""Where the sources live when the agents are installed from checkouts, and the shared libraries that come with them.

The interoperability agents import icm-lib-transforms and icm-lib-resources, and the service the standards' model
packages. Before those have releases, pip cannot resolve them from PyPI and neither can the platform's own poetry
project, which `vctl install` extends with ``poetry add``: the checkouts have to be installed editable into the
environment and recorded as path dependencies of that project."""
from __future__ import annotations

import os
from pathlib import Path

from ..venv import PlatformEnv


def checkout_root(variable: str) -> Path | None:
    for name in ((variable, 'DER_CONTROL_MODULES') if variable == 'ICM_MODULES' else (variable,)):
        if os.environ.get(name):
            return Path(os.environ[name]).expanduser()
    if variable == 'VOLTTRON_MODULAR_PROXIES' and os.environ.get('VOLTTRON_MODULAR_DRIVERS'):
        sibling = Path(os.environ['VOLTTRON_MODULAR_DRIVERS']).expanduser().parent / 'proxies'    # the modular tree: drivers/ next to proxies/
        if sibling.is_dir():
            return sibling
    return None


#: Libraries the agents need beyond their own package, and where their checkouts live when installing from source.
EXTRA_CHECKOUTS = {'volttron-lib-base-driver': ('VOLTTRON_MODULAR_DRIVERS', 'base-driver'), 'volttron-lib-fake-driver': ('VOLTTRON_MODULAR_DRIVERS', 'interfaces/fake'),
                   'protocol-proxy': ('VOLTTRON_MODULAR_PROXIES', 'protocol-proxy'), 'protocol-proxy-mqtt': ('VOLTTRON_MODULAR_PROXIES', 'mqtt'),
                   'icm-lib-transforms': ('ICM_MODULES', 'icm-utils/icm-lib-transforms'),
                   'icm-lib-resources[volttron,openfmb]': ('ICM_MODULES', 'icm-utils/icm-lib-resources'),
                   'icm-lib-models-sunspec': ('ICM_MODULES', 'icm-utils/icm-lib-models-sunspec'),
                   'icm-lib-models-ieee1815-2': ('ICM_MODULES', 'icm-utils/icm-lib-models-ieee1815-2'),
                   'icm-lib-models-ieee2030-5': ('ICM_MODULES', 'icm-utils/icm-lib-models-ieee2030-5'),
                   'icm-lib-models-openfmb': ('ICM_MODULES', 'icm-utils/icm-lib-models-openfmb')}
#: The two shared libraries every interoperability agent imports (the service, the Resource Bridge, the bus adapter).
ICM_LIBS = ('icm-lib-transforms', 'icm-lib-resources[volttron,openfmb]')
#: The standards' model packages, which the service's format universe and discovery need.
MODEL_LIBS = ('icm-lib-models-sunspec', 'icm-lib-models-ieee1815-2', 'icm-lib-models-ieee2030-5', 'icm-lib-models-openfmb')
#: The libraries without a release yet: always from icm-utils in checkout mode, whichever agent is being installed.
UNRELEASED_LIBS = ICM_LIBS + MODEL_LIBS


def split_extras(package: str) -> tuple[str, str]:
    """'name[a,b]' -> ('name', '[a,b]')."""
    return (package[:package.index('[')], package[package.index('['):]) if '[' in package else (package, '')


def library_checkouts(packages=UNRELEASED_LIBS) -> dict[str, Path]:
    """The checkouts present on this machine for the given packages: distribution name -> directory."""
    found = {}
    for package in packages:
        variable, subdir = EXTRA_CHECKOUTS[package]
        root = checkout_root(variable)
        if root is not None and (root / subdir).is_dir():
            found[package] = root / subdir
    return found


def install_library_checkouts(env: PlatformEnv, out=print, packages=UNRELEASED_LIBS) -> list[str]:
    """Install the present checkouts editable into the environment, unless that path is already its editable install.
    Returns what was installed."""
    editable = env.editable_projects()
    installed = []
    for package, path in library_checkouts(packages).items():
        name, extras = split_extras(package)
        if Path(editable.get(name, '')) != path:
            env.pip_install([str(path) + extras], out, editable=True)
            installed.append(name)
    return installed
