import pytest
from pathlib import Path

from icmstack.cli import Cli
from icmstack.components import default_registry
from icmstack.presets import PRESETS


def test_dependency_order_and_dependents():
    r = default_registry()
    order = r.ordered(['ui'])
    assert order.index('platform') < order.index('web') < order.index('ui')
    assert r.missing_needs(['ui']) == ['platform', 'web']
    assert 'web' in r.dependents('platform') and 'ui' in r.dependents('web')
    demo = r.ordered(PRESETS['demo']['components'])
    assert demo.index('broker') < demo.index('bus-adapter') and demo[0] == 'platform'
    for preset in PRESETS.values():
        for name in preset['components'] + preset.get('optional', []):
            assert name in r


def run(tmp_path, *argv, replies=(), interactive=True):
    out = []
    replies = list(replies)
    cli = Cli(out=out.append, input_fn=lambda prompt: replies.pop(0), interactive=interactive)
    code = cli.main(['--root', str(tmp_path), *argv])
    return code, '\n'.join(out)


def test_status_lists_every_component(tmp_path):
    code, text = run(tmp_path, 'status')
    assert code == 0 and 'framework:' in text and 'device:' in text
    for name in ('platform', 'ui', 'broker', 'sunspec-device', 'dnp3-outstation'):
        assert name in text
    assert text.count('not installed') >= 10


def test_config_set_and_show(tmp_path):
    code, text = run(tmp_path, 'config', '--set', 'instance_name=demo1')
    assert code == 0 and 'instance_name = demo1' in text
    code, text = run(tmp_path, 'config')
    assert 'instance_name = demo1' in text
    code, text = run(tmp_path, 'config', '--unset', 'instance_name')
    assert 'no recorded answers' in text


def test_start_asks_about_needs_and_respects_no(tmp_path, monkeypatch):
    monkeypatch.setenv('PATH', '/nonexistent')                              # no docker: the device itself refuses
    code, text = run(tmp_path, 'start', 'sep2-client', replies=['n', '', '', '', ''])      # needs declined; its four questions take the defaults
    lines = text.splitlines()
    assert not any(l.startswith(('platform:', 'driver:')) for l in lines) and 'sep2-client: docker is not available' in text


def test_sep2_identities_and_fixture(tmp_path):
    pytest.importorskip('cryptography')
    import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    from icmstack.devices import sep2
    key = ec.generate_private_key(ec.SECP256R1()); name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'dev')])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1)).sign(key, hashes.SHA256())
    path = tmp_path / 'dev.crt'; path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    lfdi = sep2.lfdi_from_cert(path)
    assert len(lfdi) == 40 and lfdi == lfdi.upper() and int(lfdi, 16)
    sfdi = sep2.sfdi_from_lfdi(lfdi)
    assert len(str(sfdi)) in (11, 12) and sum(int(d) for d in f'{sfdi:012d}') % 10 == 0
    fixture = tmp_path / 'fix' / 'boot.yaml'; sep2.write_fixture(fixture, lfdi, sfdi)
    assert f'lfdi: "{lfdi}"' in fixture.read_text() and 'op_mod_max_lim_w: 10000' in fixture.read_text()


def test_dnp3_stimulus_widens_ranges():
    from icmstack.devices import dnp3_tool
    profile = {'AI': {'points': [{'point_index': 537, 'minimum': 0, 'maximum': 100}, {'point_index': 536, 'minimum': 59.0, 'maximum': 61.0}]}}
    out = dnp3_tool.with_stimulus(profile, {537: 4200.0, 536: 60.5})
    assert dnp3_tool.get_point(out, 'AI', 537) == {'point_index': 537, 'minimum': 0, 'maximum': 4201, 'value': 4200}
    assert dnp3_tool.get_point(out, 'AI', 536)['value'] == 60.5 and dnp3_tool.get_point(profile, 'AI', 537).get('value') is None


def test_doctor_runs(tmp_path):
    code, text = run(tmp_path, 'doctor', '--no-ports')
    assert 'Python' in text and 'poetry' in text and 'ok,' in text


def test_shell_prints_environment(tmp_path):
    code, text = run(tmp_path, 'shell')
    assert code == 0 and 'export VOLTTRON_HOME=' in text and 'volttron_home' in text


def test_menu_quits(tmp_path):
    code, text = run(tmp_path, replies=[''])
    assert code == 0 and 'What now?' not in text and 'framework:' in text


def test_broker_option_is_a_question_option(tmp_path):
    code, text = run(tmp_path, '-y', 'config')
    cli = Cli(out=lambda s: None)
    parser = cli.parser()
    ns = parser.parse_args(['start', 'broker', '--broker', '127.0.0.1:1883', '--broker-port', '1884'])
    assert ns.broker == '127.0.0.1:1883' and ns.broker_port == '1884'


def test_sunspec_device_runs_and_answers_modbus(tmp_path):
    pytest.importorskip('umodbus')
    from icmstack.processes import free_port
    port = free_port()
    code, text = run(tmp_path, '-y', 'start', 'sunspec-device', '--sunspec-port', str(port))
    try:
        assert code == 0 and f'127.0.0.1:{port}' in text, text
        from umodbus.client import tcp
        import socket
        with socket.create_connection(('127.0.0.1', port), timeout=3) as sock:
            words = tcp.send_message(tcp.read_holding_registers(slave_id=1, starting_address=40004, quantity=4), sock)
        assert bytes(sum(([w >> 8, w & 0xFF] for w in words), [])).startswith(b'SunSpec')      # 1_Mn default: SunSpecTest
        code, text = run(tmp_path, 'register', 'sunspec-device', '--print')
        assert 'Modbus TCP' in text and str(port) in text
    finally:
        run(tmp_path, '-y', 'stop', 'sunspec-device')


def test_openfmb_reading_payload_round_trips_through_the_codecs():
    pytest.importorskip('icm.resources.codecs.openfmb')
    from icm.resources.codecs.openfmb import decode, encode
    from icmstack.devices.openfmb_party import DEFAULT_MRID, READING_PROTO, reading_payload
    for family in ('solar', 'ess'):
        payload = reading_payload(family, DEFAULT_MRID, w=4200.5, var=310.0, hz=60.01, phase_v=241.2)
        decoded = decode(READING_PROTO[family], encode(READING_PROTO[family], payload))
        key = 'solarReading' if family == 'solar' else 'essReading'
        assert decoded[key]['readingMMXU']['W']['net']['cVal']['mag'] == pytest.approx(4200.5)
        assert decoded['readingMessageInfo']['messageInfo']['identifiedObject']['mRID'] == DEFAULT_MRID


def test_modbus_sim_runs_and_answers(tmp_path):
    pytest.importorskip('pymodbus')
    from icmstack.processes import free_port
    port = free_port()
    code, text = run(tmp_path, '-y', 'start', 'modbus-sim', '--modbus-sim-port', str(port))
    try:
        assert code == 0 and 'modbus simulator:' in text, text
        from pymodbus.client import ModbusTcpClient
        from pymodbus.client.mixin import ModbusClientMixin as M
        c = ModbusTcpClient('127.0.0.1', port=port); assert c.connect()
        assert M.convert_from_registers(c.read_input_registers(1001, count=2).registers, M.DATATYPE.FLOAT32) == pytest.approx(412.5)
        c.close()
    finally:
        run(tmp_path, '-y', 'stop', 'modbus-sim')


def test_unexpected_errors_are_one_line(tmp_path, monkeypatch):
    import icmstack.cli as cli
    monkeypatch.setattr(cli.Cli, 'do_status', lambda self, ctx, args=None: 1 / 0)
    code, text = run(tmp_path, 'status')
    assert code == 3 and 'ZeroDivisionError' in text and '--debug' in text and 'Traceback' not in text
    with pytest.raises(ZeroDivisionError):
        run(tmp_path, '--debug', 'status')


def test_missing_cryptography_is_a_clear_message(tmp_path, monkeypatch):
    import builtins
    real_import = builtins.__import__
    def fake_import(name, *a, **k):
        if name.startswith('cryptography'):
            raise ImportError(name)
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, '__import__', fake_import)
    from icmstack.devices import sep2
    with pytest.raises(RuntimeError, match='cryptography package is missing'):
        sep2.lfdi_from_cert(tmp_path / 'x.crt')


def test_sunspec_device_serves_the_model_map_a_scan_expects():
    """"SunS" at the base, a header (ID, L) before each model, every register of each model readable, the end marker:
    pysunspec2 (and Discover) can scan the dummy; the registry's own points keep their values."""
    import sys, threading
    from pathlib import Path
    from icmstack.devices.sunspec import END_MODEL, SUNS, PointTable, SunSpecDevice, sunspec_layout
    registry = Path(__file__).resolve().parents[1] / 'src' / 'icmstack' / 'data' / 'registries' / 'sunspec_pv.modbus.csv'
    layout = sunspec_layout(PointTable(registry))
    assert (layout[40000], layout[40001]) == SUNS and (layout[40002], layout[40003]) == (1, 66)
    assert (layout[40070], layout[40071]) == (701, 153) and (layout[40225], layout[40226]) == (702, 50) and (layout[40603], layout[40604]) == (713, 7)
    assert layout[40612] == END_MODEL and layout[40613] == 0
    assert all(a in layout for a in range(40000, 40614)), 'no hole a model read could fall into'
    device = SunSpecDevice(registry, port=0).start()
    try:
        from umodbus import conf
        from umodbus.client import tcp
        import socket
        conf.SIGNED_VALUES = False
        sock = socket.create_connection((device.host, device.port), timeout=5)
        try:
            words = tcp.send_message(tcp.read_holding_registers(1, 40000, 4), sock)
            assert tuple(words[:2]) == SUNS and words[2:] == [1, 66]
            w_address = device.table.points['701_W'][0]
            assert tcp.send_message(tcp.read_holding_registers(1, w_address, 1), sock) == PointTable.to_words('int16', device.get_point('701_W'))
        finally:
            sock.close()
        try:
            import sunspec2.modbus.client as mc
        except ImportError:
            return
        d = mc.SunSpecModbusClientDeviceTCP(slave_id=1, ipaddr=device.host, ipport=device.port)
        d.scan()
        assert sorted(int(m) for m in d.models if str(m).isdigit()) == [1, 701, 702, 703, 704, 705, 706, 707, 708, 709, 710, 711, 712, 713]
        assert d.models[701][0].W.value == device.get_point('701_W')
    finally:
        device.stop()


def test_openfmb_party_addresses_controls_to_the_der_it_observes():
    """The control's mRID is the one given, else the DER whose readings the party receives (the platform's alias of the
    inverter, whatever mRID the Resources tab gave it), else the wiring tool's fixed mRID."""
    from icmstack.devices.openfmb_party import DEFAULT_MRID, WIRED_MRID, control_target, observed_mrid
    own = f'openfmb/solarmodule/SolarReadingProfile/{DEFAULT_MRID}'
    assert observed_mrid(own, own) is None
    assert observed_mrid('openfmb/solarmodule/SolarReadingProfile/e1d1-2', own) == 'e1d1-2'
    assert observed_mrid('openfmb/solarmodule/SolarControlProfile/e1d1-2', own) is None       # a control is not a DER's reading
    assert control_target('given', ['e1d1-2']) == 'given'
    assert control_target(None, ['e1d1-2', 'f2f2-3']) == 'f2f2-3'
    now = [0.0]
    observed: list[str] = []

    def sleep(s):
        now[0] += s
        if now[0] >= 1.0:
            observed.append('late-4')
    assert control_target(None, observed, wait=5.0, sleep=sleep, clock=lambda: now[0]) == 'late-4' and now[0] < 5.0
    assert control_target(None, [], wait=1.0, sleep=lambda s: now.__setitem__(0, now[0] + s), clock=lambda: now[0]) == WIRED_MRID


def test_every_subcommand_renders_its_help(capsys):
    """A question prompt may contain a percent sign ("Active power limit (%)"); argparse formats help with %, so the
    help of every subcommand that carries the questions must still render."""
    from icmstack.cli import Cli
    for command in ('up', 'start', 'restart', 'register', 'install'):
        with pytest.raises(SystemExit) as e:
            Cli(out=lambda s: None).main([command, '--help'])
        assert e.value.code == 0
        out = capsys.readouterr().out
        assert '--openfmb-control-mrid' in out and 'limit (%)' in out, command


def test_volt_var_curve_for_the_control_station():
    """The control station writes the profile's curves through the edit buffer and every AO to its associated AI's expected
    value: a Volt-Var curve becomes curve 1 and AI 297 = 1 makes it write the Volt-Var Curve Index to 1."""
    from icmstack.devices import dnp3_tool
    pts = dnp3_tool.parse_curve('92:44, 98:0,102:0,108:-44')
    assert pts == [(92.0, 44.0), (98.0, 0.0), (102.0, 0.0), (108.0, -44.0)]
    with pytest.raises(ValueError, match='at least two'):
        dnp3_tool.parse_curve('92:44')
    record = {'assoc_ao': 'AO245', 'event_class': 'Class2', 'iec_61850_uid': 'x', 'mandatory_1547': True, 'mandatory_1815': True, 'maximum': 100, 'minimum': 0,
              'multiplier': 1.0, 'name': 'n', 'offset': 0.0, 'point_index': 0, 'purpose': 'Curve', 'units': 'n/a', 'value': 0}
    template = {k: dict(record) for k in ('curve_type', 'number_of_points', 'x_units', 'y_units')}
    template.update({'curve_type': {**record, 'value': 9}, 'x_values': [dict(record)], 'y_values': [dict(record)]})
    profile = {'AI': {'points': [{'point_index': 297, 'value': 0.0, 'minimum': 0, 'maximum': 100}], 'curves': [template]},
               'AO': [{'point_index': 217}], 'BI': [], 'BO': []}
    out = dnp3_tool.with_volt_var_curve(profile, pts)
    assert set(out['AI']['curves'][0]['x_values'][0]) == set(record), 'every field the tool\'s parser requires is kept'
    assert out['AI']['curves'][0]['y_values'][3]['maximum'] == 1000
    (curve,) = out['AI']['curves']
    assert curve['curve_type']['value'] == 2 and curve['number_of_points']['value'] == 4 and curve['x_units']['value'] == 29 and curve['y_units']['value'] == 2
    assert curve['y_values'][0]['multiplier'] == 0.1 and curve['y_values'][0]['minimum'] == -1000 and curve['x_values'][0]['multiplier'] == 1.0
    assert [x['point_index'] for x in curve['x_values']] == [333, 335, 337, 339] and curve['y_values'][3]['value'] == -44.0
    assert dnp3_tool.get_point(out, 'AI', 297)['value'] == 1 and profile['AI']['curves'][0]['curve_type']['value'] == 9, 'the input profile is untouched'


def test_bundled_served_outstation_stores_its_curves():
    """The bundled served registry comes from the builder (tools/generate_dnp3_served.py): store rows for four curves,
    a selector row, no buffer rows, and the declaration carries the rule points the proxy applies."""
    import csv, json
    from icmstack.components.devices.containers import DATA
    rows = list(csv.DictReader((DATA / 'registries' / 'dnp3_der_served.dnp3.csv').open()))
    store = [r for r in rows if r['mux_area']]
    assert len(store) == 1 + 4 * 12 and {r['mux_slot'] for r in store} == {'selector', '1', '2', '3', '4'}
    assert not {r['volttron_point_name'] for r in rows if not r['mux_area']} & {'AO_244', 'AI_328', 'AO_249', 'AI_333'}
    selector = next(r for r in store if r['mux_slot'] == 'selector')
    assert (selector['index'], selector['readback_index']) == ('244', '328')
    declaration = json.loads((DATA / 'service' / 'dnp3_der.presentation.json').read_text())
    assert declaration['served_format'] == 'fake_dnp3_der_served' and len(declaration['formats']['fake_dnp3_der_served']['fields']) == len(rows)
    assert 'DVVR.VVArCrv' in {r['uid'] for r in declaration['served_remote']['mux_areas']['curve']['references']}
    assert declaration['served_equipment_specific_fields'] == {'ieee1815_2': {'curves_stored': 4}}
    served_out = next(t for t in declaration['transforms'] if (t['input_format'], t['output_format']) == ('fake_dnp3_der_served', '1815.2.outputs'))
    assert served_out['pattern']['curves']['3']['249'] == 'transform[curve3_X1]()'


def test_logs_of_a_stopped_container_component_say_so(monkeypatch):
    """docker answers "No such container" for a component that was never started; the tool says which it is and how to start it."""
    from icmstack.components import default_registry
    from icmstack.components.base import Context
    from icmstack.profile import Profile
    from icmstack.prompts import Answers
    registry = default_registry()
    profile = Profile('default', root=Path(__file__).parent / 'nope')
    ctx = Context(profile, Answers(profile, yes=True, interactive=True), out=lambda s: None)
    for name in ('dnp3-master', 'sep2-server'):
        component = registry[name]
        monkeypatch.setattr(type(component), 'running', lambda self, ctx: False)
        assert component.logs(ctx) == f'{name} is not running; icmstack start {name}'
