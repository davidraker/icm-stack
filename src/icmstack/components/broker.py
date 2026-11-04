"""The MQTT broker the bus adapter and the OpenFMB party connect to: mosquitto in docker, or one that already exists."""
from __future__ import annotations

from ..processes import Container, docker_available, port_open, wait_for
from ..prompts import Question
from .base import Component, Context, NotReady, Status

MOSQUITTO_IMAGE = 'eclipse-mosquitto:2'
MOSQUITTO_CMD = ['sh', '-c', 'printf "listener 1883\\nallow_anonymous true\\n" > /mosquitto/config/mosquitto.conf && exec mosquitto -c /mosquitto/config/mosquitto.conf']


class Broker(Component):
    name = 'broker'
    group = 'framework'
    description = 'MQTT broker (mosquitto in docker, or an existing one)'
    step = 1

    def questions(self):
        return [Question('broker', 'MQTT broker: "docker" to run mosquitto in a container, or HOST:PORT of one that already runs', default='docker', kind='str'),
                Question('broker_port', 'Host port for the mosquitto container', default=1883, kind='int')]

    def _spec(self, ctx: Context) -> tuple[str, int]:
        broker = ctx.answers.ask(self.questions()[0])
        if broker == 'docker':
            return '127.0.0.1', int(ctx.answers.ask(self.questions()[1]))
        host, _, port = str(broker).rpartition(':')
        return host or '127.0.0.1', int(port or 1883)

    def install(self, ctx: Context) -> None:
        broker = ctx.answers.ask(self.questions()[0])
        if broker == 'docker':
            if not docker_available():
                raise NotReady('docker is not available; name an existing broker as HOST:PORT instead')
            ctx.answers.ask(self.questions()[1])
        ctx.profile.mark_installed(self.name)

    def start(self, ctx: Context) -> None:
        broker = ctx.profile.answers.get('broker', 'docker')
        host, port = self._spec(ctx)
        if broker != 'docker':
            if not port_open(host, port):
                raise NotReady(f'no broker answers on {host}:{port}')
            ctx.out(f'broker: using the existing one on {host}:{port}')
            return
        container = Container(self.name, ctx.profile)
        if container.running():
            ctx.out('broker: already running')
            return
        container.run(MOSQUITTO_IMAGE, MOSQUITTO_CMD, ports={f'127.0.0.1:{port}': '1883'})    # the image's entrypoint execs the command
        if not wait_for(lambda: port_open('127.0.0.1', port), 20):
            raise NotReady(f'mosquitto did not listen on {port}:\n{container.logs()}')
        ctx.out(f'broker: mosquitto on 127.0.0.1:{port}')

    def stop(self, ctx: Context) -> None:
        if ctx.profile.answers.get('broker', 'docker') != 'docker':
            ctx.out('broker: external, left running')
            return
        if Container(self.name, ctx.profile).stop():
            ctx.out('broker: stopped')

    def status(self, ctx: Context) -> Status:
        installed = ctx.profile.is_installed(self.name)
        broker = ctx.profile.answers.get('broker', 'docker')
        if broker != 'docker':
            host, _, port = str(broker).rpartition(':')
            up = port_open(host or '127.0.0.1', int(port or 1883))
            return Status(installed, up, f'external {broker}')
        port = ctx.profile.answers.get('broker_port', 1883)
        running = Container(self.name, ctx.profile).running() if installed else False
        return Status(installed, running, f'mosquitto 127.0.0.1:{port}' if installed else '')

    def logs(self, ctx: Context, lines: int = 40) -> str:
        return Container(self.name, ctx.profile).logs(lines)
