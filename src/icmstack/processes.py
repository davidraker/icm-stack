"""Long-running pieces: child processes the tool starts detached (pidfile, log file) and docker containers."""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from .profile import Profile


class ProcessError(Exception):
    pass


def pid_alive(pid: int) -> bool:
    try:
        os.waitpid(pid, os.WNOHANG)      # reap it when it is our own finished child, so it does not linger as a zombie
    except ChildProcessError:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        state = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0]
        return state != 'Z'              # a zombie is finished, whoever its parent is
    except OSError:
        return True


class ManagedProcess:
    """A child started in its own session with stdout and stderr in ``logs/<name>.log`` and its pid in ``pids/<name>.pid``.
    The tool itself never daemonises; the child outlives it."""

    def __init__(self, name: str, profile: Profile):
        self.name, self.profile = name, profile
        self.pidfile = profile.pids / f'{name}.pid'
        self.logfile = profile.logs / f'{name}.log'

    def pid(self) -> int | None:
        try:
            pid = int(self.pidfile.read_text().strip())
        except (OSError, ValueError):
            return None
        if pid_alive(pid):
            return pid
        self.pidfile.unlink(missing_ok=True)    # a pidfile whose process is gone is stale, not running
        return None

    def running(self) -> bool:
        return self.pid() is not None

    def start(self, cmd: list[str], env: dict | None = None, cwd: str | Path | None = None) -> int:
        if self.running():
            raise ProcessError(f'{self.name} is already running (pid {self.pid()})')
        self.profile.ensure()
        log = open(self.logfile, 'ab')
        full_env = env if env is not None and 'PATH' in env else {**os.environ, **(env or {})}    # a complete environment is used as given
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                env=full_env, cwd=str(cwd) if cwd else None, start_new_session=True)
        self.pidfile.write_text(str(proc.pid))
        return proc.pid

    def stop(self, timeout: float = 20.0) -> bool:
        """SIGINT, then SIGTERM, then SIGKILL to the child's process group. True when something was stopped."""
        pid = self.pid()
        if pid is None:
            return False
        for sig, wait in ((signal.SIGINT, timeout), (signal.SIGTERM, 5.0), (signal.SIGKILL, 2.0)):
            try:
                os.killpg(pid, sig)
            except ProcessLookupError:
                break
            deadline = time.time() + wait
            while time.time() < deadline and pid_alive(pid):
                time.sleep(0.1)
            if not pid_alive(pid):
                break
        self.pidfile.unlink(missing_ok=True)
        return True

    def tail(self, lines: int = 40) -> str:
        if not self.logfile.exists():
            return ''
        return ''.join(self.logfile.read_text(errors='replace').splitlines(keepends=True)[-lines:])


def docker_available() -> bool:
    return shutil.which('docker') is not None and subprocess.run(['docker', 'info'], capture_output=True).returncode == 0


def docker_has_image(image: str) -> bool:
    return subprocess.run(['docker', 'image', 'inspect', image], capture_output=True).returncode == 0


def docker_has_volume(volume: str) -> bool:
    return subprocess.run(['docker', 'volume', 'inspect', volume], capture_output=True).returncode == 0


class Container:
    """A docker container the tool owns, named ``icmstack-<profile>-<component>``."""

    def __init__(self, component: str, profile: Profile):
        self.name = f'icmstack-{profile.name}-{component}'

    def running(self) -> bool:
        done = subprocess.run(['docker', 'inspect', '-f', '{{.State.Running}}', self.name], capture_output=True, text=True)
        return done.returncode == 0 and done.stdout.strip() == 'true'

    def run(self, image: str, args: list[str] = (), *, ports: dict[str, str] | None = None, volumes: dict[str, str] | None = None,
            network: str | None = None, extra: list[str] = (), entrypoint: list[str] = ()) -> None:
        subprocess.run(['docker', 'rm', '-f', self.name], capture_output=True)
        cmd = ['docker', 'run', '-d', '--name', self.name]
        if network:
            cmd += ['--network', network]
        for host, inner in (ports or {}).items():
            cmd += ['-p', f'{host}:{inner}']
        for src, dst in (volumes or {}).items():
            cmd += ['-v', f'{src}:{dst}']
        cmd += list(extra) + list(entrypoint) + [image] + list(args)
        done = subprocess.run(cmd, capture_output=True, text=True)
        if done.returncode != 0:
            raise ProcessError(f'docker run failed for {self.name}: {done.stderr.strip()}')

    def stop(self) -> bool:
        existed = subprocess.run(['docker', 'inspect', self.name], capture_output=True).returncode == 0
        subprocess.run(['docker', 'rm', '-f', self.name], capture_output=True)
        return existed

    def logs(self, lines: int = 40) -> str:
        done = subprocess.run(['docker', 'logs', '--tail', str(lines), self.name], capture_output=True, text=True)
        return done.stdout + done.stderr


def port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    import socket
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except OSError:
        return False


def wait_for(predicate, timeout: float, interval: float = 0.3) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def processes_with_env(variable: str, value: str, needle: str = '') -> list[tuple[int, str]]:
    """(pid, command line) of processes whose environment has variable=value and whose command line contains needle:
    how a platform bound to a home is found even when no pidfile points at it (an orphan of a deleted profile)."""
    found = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            environ = (entry / 'environ').read_bytes().split(b'\0')
            cmdline = (entry / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace').strip()
        except OSError:
            continue
        if f'{variable}={value}'.encode() in environ and needle in cmdline:
            found.append((int(entry.name), cmdline))
    return found


def stop_pid(pid: int, timeout: float = 20.0) -> bool:
    for sig, wait in ((signal.SIGINT, timeout), (signal.SIGTERM, 5.0), (signal.SIGKILL, 2.0)):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return True
        deadline = time.time() + wait
        while time.time() < deadline and pid_alive(pid):
            time.sleep(0.1)
        if not pid_alive(pid):
            return True
    return not pid_alive(pid)
