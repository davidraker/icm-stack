"""The SunSpec inverter dummy device: a uModbus TCP slave run from this tool's own interpreter."""
from __future__ import annotations

import sys
from pathlib import Path

from ...processes import ManagedProcess, port_open, wait_for
from ...prompts import Question
from ..base import Component, Context, NotReady, Status

DATA = Path(__file__).resolve().parents[2] / 'data' / 'registries'


class SunSpecDevice(Component):
    name = 'sunspec-device'
    group = 'device'
    description = 'SunSpec inverter (uModbus TCP)'
    step = 3

    Q_HOST = Question('sunspec_host', 'SunSpec device host', default='127.0.0.1')
    Q_PORT = Question('sunspec_port', 'SunSpec device port', default=5020, kind='int')
    Q_UNIT = Question('sunspec_unit', 'SunSpec device unit id', default=1, kind='int')
    Q_REGISTRY = Question('sunspec_registry', 'SunSpec registry CSV', default=str(DATA / 'sunspec_pv.modbus.csv'), kind='path',
                          help='the points the device serves and their default values; the bundled file is the e2e PV inverter')
    Q_TOPIC = Question('sunspec_topic', 'Topic of the SunSpec device in the platform (devices/<topic>, used by register)', default='icm/sunspec/inverter')
    Q_LIVE = Question('sunspec_live', 'Move the inverter\'s meter readings along slow waves (no: static registry defaults)', default=True, kind='bool')

    def questions(self):
        return [self.Q_HOST, self.Q_PORT, self.Q_UNIT, self.Q_REGISTRY, self.Q_TOPIC, self.Q_LIVE]

    def install(self, ctx: Context) -> None:
        try:
            import umodbus  # noqa: F401
        except ImportError:
            raise NotReady('the umodbus package is missing in icmstack\'s environment: pip install "icmstack[devices]"')
        for q in (self.Q_HOST, self.Q_PORT, self.Q_UNIT, self.Q_REGISTRY, self.Q_LIVE):
            ctx.answers.ask(q)
        if not Path(ctx.profile.answers['sunspec_registry']).exists():
            raise NotReady(f'registry not found: {ctx.profile.answers["sunspec_registry"]}')

    def process(self, ctx: Context) -> ManagedProcess:
        return ManagedProcess(self.name, ctx.profile)

    def start(self, ctx: Context) -> None:
        proc = self.process(ctx)
        if proc.running():
            ctx.out('sunspec-device: already running')
            return
        a = ctx.profile.answers
        proc.start([sys.executable, '-m', 'icmstack.devices.sunspec', '--registry', a['sunspec_registry'], '--host', a['sunspec_host'],
                    '--port', str(a['sunspec_port']), '--unit', str(a['sunspec_unit']), '--live' if a.get('sunspec_live', True) else '--no-live'])
        if not wait_for(lambda: port_open(a['sunspec_host'], int(a['sunspec_port'])) or not proc.running(), 20):
            raise NotReady(f'sunspec-device did not listen:\n{proc.tail(10)}')
        if not proc.running():
            raise NotReady(f'sunspec-device exited:\n{proc.tail(10)}')
        ctx.out(f'sunspec-device: Modbus TCP on {a["sunspec_host"]}:{a["sunspec_port"]}, unit {a["sunspec_unit"]}')

    def stop(self, ctx: Context) -> None:
        if self.process(ctx).stop():
            ctx.out('sunspec-device: stopped')

    def status(self, ctx: Context) -> Status:
        installed = ctx.profile.is_installed(self.name)
        a = ctx.profile.answers
        return Status(installed, self.process(ctx).running() if installed else False,
                      f'{a.get("sunspec_host")}:{a.get("sunspec_port")} unit {a.get("sunspec_unit")}' if installed else '')

    def logs(self, ctx: Context, lines: int = 40) -> str:
        return self.process(ctx).tail(lines)

    def connection_details(self, ctx: Context) -> str:
        a = ctx.profile.answers
        return (f'sunspec-device: Modbus TCP, host {a.get("sunspec_host", "127.0.0.1")}, port {a.get("sunspec_port", 5020)}, '
                f'unit id {a.get("sunspec_unit", 1)}; registry {a.get("sunspec_registry")}; suggested topic {a.get("sunspec_topic", self.Q_TOPIC.default)}')

    def register(self, ctx: Context) -> None:
        """The registry, a Modbus client device entry and a canonical resource in the device's format, with the format
        and its transforms to and from the SunSpec hub declared in the service (bundled with the registry)."""
        import json
        from ...platform_config import canonical_mapping, device_entry, merge_service_config, store_csv, store_json
        from ..platform_modular import Platform
        if not Platform().process(ctx).running():
            raise NotReady('the platform is not running')
        a = ctx.profile.answers
        for q in (self.Q_HOST, self.Q_PORT, self.Q_UNIT, self.Q_REGISTRY, self.Q_TOPIC):
            ctx.answers.ask(q)
        topic = a['sunspec_topic'].strip('/')
        registry = Path(a['sunspec_registry'])
        registry_name = f'{topic.rsplit("/", 1)[-1]}.csv'
        store_csv(ctx, 'platform.driver', f'registry_configs/{registry_name}', registry)
        store_json(ctx, 'platform.driver', f'devices/{topic}', device_entry('modbus', registry_name, {
            'transport_protocol': 'tcp', 'device_address': a['sunspec_host'], 'port': int(a['sunspec_port']), 'unit_id': int(a['sunspec_unit']), 'timeout': 5}))
        declaration = DATA.parent / 'service' / 'sunspec_pv.presentation.json'
        data_format = 'sunspec'
        formats, transforms = {}, []
        if registry.resolve() == (DATA / 'sunspec_pv.modbus.csv').resolve() and declaration.exists():
            spec = json.loads(declaration.read_text())
            formats, transforms = spec.get('formats', {}), spec.get('transforms', [])
            data_format = next(iter(formats), data_format)
        merge_service_config(ctx, 'platform.presentation', formats, transforms, [canonical_mapping(topic, data_format)])
        ctx.out(f'sunspec-device: registered as devices/{topic} (registry {registry_name}, resource {topic} in format {data_format})')
