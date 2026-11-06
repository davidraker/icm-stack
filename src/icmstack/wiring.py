"""The showcases: representative paths through the framework, each a set of aliases, bridges and bus adapter entries
written into the platform. The same table wires a path (``icmstack wire NAME``), prints the settings to make it by hand
in the UI (``icmstack wire --print NAME``), and is the source of SHOWROOM_USE_CASE_GUIDE.md."""
from __future__ import annotations

from dataclasses import dataclass, field

from .components.base import Context, NotReady
from .platform_config import get_json, list_entries, merge_service_config, store_json

ALIAS_MRID = '7d1a2b3c-0000-4000-8000-000000000011'      # the platform's OpenFMB identity for the SunSpec inverter
ESS_MRID = '5e6f7a80-0000-4000-8000-000000000013'        # the platform's OpenFMB identity for the served 2030.5 DER
SERVICE, DRIVER, BRIDGE, BUS = 'platform.presentation', 'platform.driver', 'platform.resource_bridge', 'platform.bus_adapter'
# A bridge's "points" names SOURCE points: what of the source's publication is carried. A control bridge carries the
# controller's setpoint points only, so its other data does not overwrite the target.
DNP3_CONTROL_POINTS = ['AO_87', 'BO_17']                     # active power limit setpoint and its enable
SEP2_CONTROL_POINTS = ['DERControl_opModMaxLimW']


def openfmb_uai(family: str, kind: str, mrid: str) -> list[str]:
    profile = 'Solar' if family == 'solar' else 'ESS'
    return ['openfmb', f'{family}module', f'{profile}{"Reading" if kind == "reading" else "Control"}Profile', mrid]


@dataclass
class Showcase:
    name: str
    title: str
    kind: str                                  # telemetry | control
    needs: tuple[str, ...]                     # device components whose registration must exist (their topic answers)
    description: str
    observe: str                               # how to see it working
    aliases: list = field(default_factory=list)         # (uai, format, references_topic_key, mrid)
    bridges: list = field(default_factory=list)         # dicts with source/target as ('device', topic_key) or ('bus', uai) and optional points
    outbound: list = field(default_factory=list)        # alias UAIs the bus adapter publishes to the broker
    inbound: list = field(default_factory=list)         # alias UAIs the bus adapter subscribes to on the broker


def _solar_reading(): return openfmb_uai('solar', 'reading', ALIAS_MRID)
def _solar_control(): return openfmb_uai('solar', 'control', ALIAS_MRID)
def _ess_reading(): return openfmb_uai('ess', 'reading', ESS_MRID)


SHOWCASES: dict[str, Showcase] = {s.name: s for s in [
    Showcase('sunspec-to-openfmb', 'SunSpec inverter readings published as an OpenFMB SolarReadingProfile', 'telemetry', ('sunspec-device', 'openfmb-party'),
             'The driver polls the inverter; the Resource Bridge publishes the readings on the local bus as the OpenFMB alias; the bus adapter relays them to the broker.',
             'icmstack logs openfmb-party: "received openfmb/solarmodule/SolarReadingProfile/…0011 as solarmodule.SolarReadingProfile" every poll (W, Hz from the inverter).',
             aliases=[(_solar_reading(), 'openfmb.solar.reading', 'sunspec_topic', ALIAS_MRID)],
             bridges=[{'name': 'sunspec>openfmb.reading', 'source': ('device', 'sunspec_topic'), 'target': ('bus', _solar_reading())}],
             outbound=[_solar_reading()]),
    Showcase('openfmb-to-sunspec', 'An OpenFMB SolarControlProfile from the broker limits the SunSpec inverter', 'control', ('sunspec-device', 'openfmb-party'),
             'The bus adapter subscribes to the control alias on the broker; the Resource Bridge translates a received control into writes of the inverter\'s limit points.',
             'icmstack start openfmb-party --openfmb-control-limit 50 publishes a 50 % limit; the inverter\'s 704_WMaxLimPct register changes (icmstack logs sunspec-device shows the write; vctl driver get <topic>/704_WMaxLimPct).',
             aliases=[(_solar_control(), 'openfmb.solar.control', 'sunspec_topic', ALIAS_MRID)],
             bridges=[{'name': 'openfmb.control>sunspec', 'source': ('bus', _solar_control()), 'target': ('device', 'sunspec_topic')}],
             inbound=[_solar_control()]),
    Showcase('sunspec-to-dnp3', 'SunSpec inverter readings served to a DNP3 control station', 'telemetry', ('sunspec-device', 'dnp3-master'),
             'The Resource Bridge writes the inverter\'s readings into the DNP3 outstation the platform serves (SunSpec -> 1815.2 hub -> served DNP3 format); the external control station polls it.',
             'icmstack logs dnp3-master: the control station\'s reads show the inverter\'s W and Hz as AI 537 and 536 (scaled by the profile).',
             bridges=[{'name': 'sunspec>dnp3.served', 'source': ('device', 'sunspec_topic'), 'target': ('device', 'dnp3_served_topic')}]),
    Showcase('dnp3-to-sunspec', 'A DNP3 control station\'s setpoint limits the SunSpec inverter', 'control', ('sunspec-device', 'dnp3-master'),
             'The control station writes AO 87 (active power limit) on the served outstation; the Resource Bridge carries it to the inverter\'s limit points.',
             'icmstack start dnp3-master --dnp3-master-limit 40 makes the control station write AO87; the inverter\'s 704_WMaxLimPct follows (vctl driver get <topic>/704_WMaxLimPct).',
             bridges=[{'name': 'dnp3.served>sunspec', 'source': ('device', 'dnp3_served_topic'), 'target': ('device', 'sunspec_topic'), 'points': DNP3_CONTROL_POINTS}]),
    Showcase('sunspec-to-sep2', 'SunSpec inverter readings reported to a 2030.5 utility server', 'telemetry', ('sunspec-device', 'sep2-server'),
             'The Resource Bridge feeds the platform\'s 2030.5 client device with the inverter\'s readings; the client posts MirrorMeterReadings to the Go server.',
             'The Go server\'s traffic capture under <profile>/sep2/capture grows with MirrorMeterReading posts; icmstack logs sep2-server shows the requests.',
             bridges=[{'name': 'sunspec>sep2', 'source': ('device', 'sunspec_topic'), 'target': ('device', 'sep2_topic')}]),
    Showcase('sep2-to-sunspec', 'A 2030.5 DERControl from the utility server limits the SunSpec inverter', 'control', ('sunspec-device', 'sep2-server'),
             'The platform\'s 2030.5 client receives a DERControl (opModMaxLimW) from the Go server; the Resource Bridge carries it to the inverter\'s limit points.',
             'icmstack control sep2-server --max-lim-w 5000 creates the control through the server\'s admin API; the inverter\'s 704_WMaxLimPct follows.',
             bridges=[{'name': 'sep2>sunspec', 'source': ('device', 'sep2_topic'), 'target': ('device', 'sunspec_topic'), 'points': SEP2_CONTROL_POINTS}]),
    Showcase('sep2client-to-openfmb', 'A 2030.5 DER client\'s reports published as an OpenFMB ESSReadingProfile', 'telemetry', ('sep2-client', 'openfmb-party'),
             'The Go DER client reports to the 2030.5 server the platform serves; the Resource Bridge publishes that served device\'s readings as an OpenFMB ESS alias relayed to the broker.',
             'icmstack logs openfmb-party: "received openfmb/essmodule/ESSReadingProfile/…0013" with the Go client\'s simulated PV power.',
             aliases=[(_ess_reading(), 'openfmb.ess.reading', 'sep2_served_topic', ESS_MRID)],
             bridges=[{'name': 'sep2.served>openfmb.reading', 'source': ('device', 'sep2_served_topic'), 'target': ('bus', _ess_reading())}],
             outbound=[_ess_reading()]),
]}

DEMO_SHOWCASES = ['sunspec-to-openfmb', 'openfmb-to-sunspec']
MATRIX_SHOWCASES = list(SHOWCASES)


def _topic(ctx: Context, key: str) -> str:
    return str(ctx.profile.answers.get(key) or '').strip('/')


def _end(ctx: Context, end) -> dict | list:
    kind, value = end
    if kind == 'bus':
        return {'uai': list(value), 'bus': True}
    topic = _topic(ctx, value)
    if not topic or f'devices/{topic}' not in list_entries(ctx, DRIVER):
        raise NotReady(f'the device for {value} ({topic or "no topic answered"}) is not registered')
    return topic.split('/')


def wire(ctx: Context, names: list[str]) -> None:
    """Write the aliases, bridges and bus adapter entries of the named showcases. Idempotent: entries are replaced by
    name (bridges), by UAI (aliases) and by topic (subscriptions)."""
    aliases, bridges, outbound, inbound = [], [], [], []
    for name in names:
        s = SHOWCASES[name]
        for uai, fmt, ref_key, mrid in s.aliases:
            ref = _end(ctx, ('device', ref_key))
            aliases.append({'uai': list(uai), 'resource_type': 'alias', 'resource': {'data_format': fmt, 'owner': DRIVER, 'references': ref, 'encoding': 'protobuf',
                                                                                     'parameters': {'mrid': mrid, 'name': ' '.join(ref)}}})
        for b in s.bridges:
            entry = {'name': b['name'], 'source': _end(ctx, b['source']), 'target': _end(ctx, b['target'])}
            if b.get('points'):
                entry['points'] = list(b['points'])
            bridges.append(entry)
        outbound += ['/'.join(u) for u in s.outbound]
        inbound += ['/'.join(u) for u in s.inbound]
    if aliases:
        merge_service_config(ctx, SERVICE, mappings=aliases)
    if bridges:
        cfg = get_json(ctx, BRIDGE, 'config') or {}
        cfg.setdefault('presentation_service', SERVICE); cfg.setdefault('driver', DRIVER); cfg.setdefault('echo_window', 5.0)
        names_now = {b['name'] for b in bridges}
        cfg['bridges'] = [b for b in cfg.get('bridges', []) if b.get('name') not in names_now] + bridges
        store_json(ctx, BRIDGE, 'config', cfg)
    if outbound or inbound:
        host, port = _broker(ctx)
        cfg = get_json(ctx, BUS, 'config') or {}
        cfg.setdefault('bus_type', 'mqtt'); cfg.setdefault('proxy_registration_timeout', 60)
        adapters = cfg.get('adapters', [])
        site = next((a for a in adapters if (a.get('name') or a.get('host')) == 'site'), None)
        if site is None:
            site = {'name': 'site'}; adapters.append(site)
        site.update({'host': host, 'port': port})
        site['local_subscriptions'] = sorted(set(site.get('local_subscriptions', [])) | set(outbound))
        site['remote_subscriptions'] = sorted(set(site.get('remote_subscriptions', [])) | set(inbound))
        cfg['adapters'] = adapters
        store_json(ctx, BUS, 'config', cfg)
    for name in names:
        ctx.out(f'wiring: {name}: {SHOWCASES[name].title}')


def wire_demo(ctx: Context) -> None:
    wire(ctx, DEMO_SHOWCASES)


def describe(ctx: Context | None, name: str) -> str:
    """The settings to make the showcase by hand in the UI: one block per form."""
    s = SHOWCASES[name]
    topic = (lambda key: (_topic(ctx, key) if ctx else None) or {'sunspec_topic': 'icm/sunspec/inverter', 'dnp3_served_topic': 'icm/dnp3/served',
                                                               'sep2_topic': 'icm/sep2/der', 'sep2_served_topic': 'icm/sep2/served'}.get(key, key))
    lines = [f'{name}: {s.title}', f'  kind: {s.kind}; devices: {", ".join(s.needs)}', f'  {s.description}', '']
    for uai, fmt, ref_key, mrid in s.aliases:
        lines += [f'  Resources tab, select {topic(ref_key)}, "+ Alias":', f'    UAI: {" / ".join(uai)}', f'    Format: {fmt}', '    Encoding: protobuf (edition empty)',
                  f'    Delivery: a remote topic on a bus; bus: site; direction: {"outbound" if list(uai) in [list(u) for u in s.outbound] else "inbound"}',
                  f'    (Save writes the alias with mrid parameter {mrid} and the bus adapter\'s subscription; the Advanced "Static parameters" of the alias carry mrid)', '']
    for b in s.bridges:
        src = f'bus topic {"/".join(b["source"][1])}' if b['source'][0] == 'bus' else f'device {topic(b["source"][1])}'
        dst = f'bus topic {"/".join(b["target"][1])}' if b['target'][0] == 'bus' else f'device {topic(b["target"][1])}'
        lines += ['  Bridges tab, "+ Device bridge":', f'    Source · read from: {src}', f'    Target · written to: {dst}', '    write unchanged: off']
        if b.get('points'):
            lines += [f'    Points (source points carried): {", ".join(b["points"])}']
        lines += ['']
    lines += [f'  Observe: {s.observe}']
    return '\n'.join(lines)


def _broker(ctx: Context) -> tuple[str, int]:
    spec = ctx.profile.answers.get('broker', 'docker')
    if spec == 'docker':
        return '127.0.0.1', int(ctx.profile.answers.get('broker_port', 1883))
    host, _, port = str(spec).rpartition(':')
    return host or '127.0.0.1', int(port or 1883)
