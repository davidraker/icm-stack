"""The IEEE 1815.2 test tool's reference outstation and reference control station as independent DNP3 parties: Rust
binaries from the tool's cargo-cache volume, run in its backend image on the host network. From the real-platform e2e
harness. The tool checkout is named by ``IEEE1815_2_TEST_TOOL`` (its ``data/profiles/full.json`` is the DER profile)."""
from __future__ import annotations

import copy
import json
import math
import os
import re
import subprocess
import time
from pathlib import Path

IMAGE = 'ieee-std-1815-2-test-tool-backend-dev:latest'
CACHE_VOLUME = 'ieee-std-1815-2-test-tool_cargo-cache'
ANSI = re.compile(r'\x1b\[[0-9;]*m')


def tool_repo() -> Path | None:
    value = os.environ.get('IEEE1815_2_TEST_TOOL')
    return Path(value).expanduser() if value else None


def available() -> tuple[bool, str]:
    repo = tool_repo()
    if repo is None or not (repo / 'data' / 'profiles' / 'full.json').is_file():
        return False, 'set IEEE1815_2_TEST_TOOL to a checkout of the IEEE 1815.2 test tool (its data/profiles/full.json is the DER profile)'
    if subprocess.run(['docker', 'image', 'inspect', IMAGE], capture_output=True).returncode != 0:
        return False, f'docker image {IMAGE} is missing: build the test tool once with its compose file'
    if subprocess.run(['docker', 'volume', 'inspect', CACHE_VOLUME], capture_output=True).returncode != 0:
        return False, f'docker volume {CACHE_VOLUME} is missing: it holds the compiled reference binaries; build the tool once'
    return True, ''


def load_profile() -> dict:
    repo = tool_repo()
    if repo is None:
        raise RuntimeError('Set IEEE1815_2_TEST_TOOL to a checkout of the IEEE 1815.2 test tool.')
    return json.loads((repo / 'data' / 'profiles' / 'full.json').read_text())


def _points(profile: dict, table: str):
    section = profile[table]
    if isinstance(section, list):
        return section
    for value in section.values():
        if isinstance(value, list):
            return value
    raise KeyError(table)


def get_point(profile: dict, table: str, index: int) -> dict:
    for point in _points(profile, table):
        if int(point.get('point_index', -1)) == index:
            return point
    raise KeyError(f'{table} {index}')


def set_point(profile: dict, table: str, index: int, **fields) -> dict:
    point = get_point(profile, table, index)
    point.update(fields)
    return profile


def widen(profile: dict, index: int, value: float):
    """Set an analog input's value, widening its range to include it; integer bounds stay integers."""
    point = get_point(profile, 'AI', index)
    low, high = point.get('minimum', 0), point.get('maximum', 0)
    cast = int if isinstance(low, int) and isinstance(high, int) and float(value).is_integer() else float
    set_point(profile, 'AI', index, value=cast(value) if cast is int else value,
              minimum=cast(min(low, value - 1)), maximum=cast(max(high, value + 1)))


def with_stimulus(profile: dict, analog_values: dict[int, float] | None = None, ao_targets: dict[int, float] | None = None) -> dict:
    """A copy of the profile with analog input values (what the reference outstation serves) and analog output targets:
    the value of an AO's associated AI, which the control station writes to that AO in engineering units."""
    p = copy.deepcopy(profile)
    for index, value in (analog_values or {}).items():
        widen(p, index, value)
    for index, value in (ao_targets or {}).items():
        ai = get_point(p, 'AO', index).get('assoc_ai')
        if not ai:
            raise KeyError(f'AO {index} has no associated AI in the profile')
        widen(p, int(re.sub(r'\D', '', str(ai))), value)
    return p


def trimmed(profile: dict, ai=(), ao=(), bi=(), bo=()) -> dict:
    """A copy keeping only the listed point indices per table: a control station on an AI-only profile reads and never
    writes; one with AO 87 and BO 17 writes just those."""
    p = copy.deepcopy(profile)
    for table, indices in (('AI', set(ai)), ('AO', set(ao)), ('BI', set(bi)), ('BO', set(bo))):
        section = p[table]
        if isinstance(section, list):
            p[table] = [pt for pt in section if int(pt.get('point_index', -1)) in indices]
        else:
            p[table] = {k: ([pt for pt in v if int(pt.get('point_index', -1)) in indices] if isinstance(v, list) else v) for k, v in section.items()}
    return p


class Container:
    def __init__(self, name: str, profile: dict, work: Path, log_level: str = 'info'):
        self.name, self.work, self.log_level = name, Path(work), log_level
        self.work.mkdir(parents=True, exist_ok=True)
        self.profile_path = self.work / f'{name}.profile.json'
        self.profile_path.write_text(json.dumps(profile))

    def run(self, binary: str, *args: str) -> None:
        subprocess.run(['docker', 'rm', '-f', self.name], capture_output=True)
        cmd = ['docker', 'run', '-d', '--name', self.name, '--network', 'host', '-v', f'{CACHE_VOLUME}:/t:ro',
               '-v', f'{self.work}:/work:ro', IMAGE, f'/t/debug/{binary}', '--profile', f'/work/{self.profile_path.name}',
               '--log-level', self.log_level, *args]
        done = subprocess.run(cmd, capture_output=True, text=True)
        if done.returncode != 0:
            raise RuntimeError(f'docker run failed for {self.name}: {done.stderr.strip()}')

    def logs(self, lines: int | None = None) -> str:
        cmd = ['docker', 'logs'] + (['--tail', str(lines)] if lines else []) + [self.name]
        done = subprocess.run(cmd, capture_output=True, text=True)
        return ANSI.sub('', done.stdout + done.stderr)

    def running(self) -> bool:
        done = subprocess.run(['docker', 'inspect', '-f', '{{.State.Running}}', self.name], capture_output=True, text=True)
        return done.returncode == 0 and done.stdout.strip() == 'true'

    def stop(self) -> bool:
        existed = subprocess.run(['docker', 'inspect', self.name], capture_output=True).returncode == 0
        subprocess.run(['docker', 'rm', '-f', self.name], capture_output=True)
        return existed


def start_outstation(container: Container, port: int, outstation_address: int = 1, master_address: int = 2, timeout: float = 30.0) -> None:
    """The tool's outstation on 127.0.0.1:port: the external DNP3 device the platform's master polls and operates."""
    import socket
    container.run('reference-outstation', '--local', f'127.0.0.1:{port}', '--outstation-address', str(outstation_address),
                  '--master-address', str(master_address), '--conformance-loop-interval', '3600')
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            socket.create_connection(('127.0.0.1', port), timeout=0.5).close()
            return
        except OSError:
            time.sleep(0.3)
    raise RuntimeError(f'reference outstation did not listen on {port}:\n{container.logs(40)}')


def start_control_station(container: Container, port: int, outstation_address: int = 1, master_address: int = 2) -> None:
    """The tool's master polling the outstation at 127.0.0.1:port (the one the platform serves)."""
    container.run('reference-control-station', '--outstation-ip', '127.0.0.1', '--outstation-port', str(port),
                  '--outstation-address', str(outstation_address), '--control-station-address', str(master_address),
                  '--control-loop-interval', '5', '--conformance-loop-interval', '5')
