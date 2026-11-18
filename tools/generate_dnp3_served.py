"""Regenerate icmstack's bundled served DNP3 outstation from the IEEE 1815.2 point list.

The served registry (``data/registries/dnp3_der_served.dnp3.csv``) and the served half of the service declaration
(``data/service/dnp3_der.presentation.json``: the format ``fake_dnp3_der_served``, its transforms to and from the
1815.2 hubs, the remote-config extras and the stored-instance summary) come from the interoperability service's
builder: the IEEE 1547 mandatory subset, with the curve edit buffer backed by four stored curves of four pairs and the
schedule areas left out, which is what the showroom's control station exercises. The client half of the declaration
is kept as it is.

    python tools/generate_dnp3_served.py

Needs the interoperability service importable (it carries the builder and the point list through icm-lib-models).
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / 'src' / 'icmstack' / 'data'
FORMAT = 'fake_dnp3_der_served'
TOPIC = 'icm/dnp3/served'


def main() -> int:
    from interoperability.discovery.service import discover
    result = discover('dnp3', {'topic': TOPIC, 'role': 'server', 'conformance': '1547', 'bind_host': '127.0.0.1', 'port': 20001,
                               'compose': {'functions': [], 'curves': {'pairs': 4, 'stored': 4}, 'schedules': {}, 'equipment': []}}, None)
    rows = next(e for e in result['entries'] if e['type'] == 'csv')['data']
    device = next(e for e in result['entries'] if e['name'].startswith('devices/'))['data']
    fragment = next(e for e in result['entries'] if e.get('fragment'))['fragment']
    stem = TOPIC.split('/')[-1]
    declaration_path = DATA / 'service' / 'dnp3_der.presentation.json'
    declaration = json.loads(declaration_path.read_text())
    declaration['formats'][FORMAT] = fragment['formats'][stem]
    transforms = [t for t in declaration['transforms'] if FORMAT not in (t.get('input_format'), t.get('output_format'))]
    for t in fragment['transforms']:
        transforms.append({**t, 'input_format': FORMAT if t['input_format'] == stem else t['input_format'],
                           'output_format': FORMAT if t['output_format'] == stem else t['output_format']})
    declaration['transforms'] = transforms
    declaration['served_format'] = FORMAT
    declaration['served_remote'] = {'mux_areas': device['remote_config'].get('mux_areas', {})}
    declaration['served_equipment_specific_fields'] = device.get('equipment_specific_fields', {})
    declaration['served_notes'] = result['notes']
    declaration_path.write_text(json.dumps(declaration, indent=2) + '\n')
    registry_path = DATA / 'registries' / 'dnp3_der_served.dnp3.csv'
    with registry_path.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    stores = [r for r in rows if r['mux_area']]
    print(f'{registry_path.name}: {len(rows)} rows ({len(stores)} store and selector rows); {declaration_path.name}: format {FORMAT} with {len(declaration["formats"][FORMAT]["fields"])} fields')
    for note in result['notes']:
        print(f'  note: {note}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
