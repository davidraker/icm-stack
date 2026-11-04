"""A SunSpec inverter as a Modbus TCP slave (uModbus): the registry's points at their addresses, seeded from the
registry's default values. Runnable: ``python -m icmstack.devices.sunspec --registry FILE --port 5020``.
From the real-platform e2e harness (interoperability_service/tests/e2e_platform/external/sunspec_device.py)."""
from __future__ import annotations

import argparse
import csv
import re
import struct
import sys
import threading
import time
from pathlib import Path
from socketserver import ThreadingTCPServer

WIDTHS = {'int16': 1, 'uint16': 1, 'sunssf': 1, 'enum16': 1, 'bitfield16': 1, 'acc16': 1, 'pad': 1,
          'int32': 2, 'uint32': 2, 'acc32': 2, 'bitfield32': 2, 'enum32': 2, 'float32': 2, 'int64': 4, 'uint64': 4, 'acc64': 4}


class PointTable:
    """The registry's points: name -> (address, data type, register count), and the default values it carries."""

    def __init__(self, registry_csv: Path):
        self.points: dict[str, tuple[int, str, int]] = {}
        self.defaults: dict[str, object] = {}
        with open(registry_csv, newline='') as f:
            for row in csv.DictReader(f):
                name = row.get('volttron_point_name') or row.get('Volttron Point Name') or row.get('Point Name')
                address = int(row.get('address') or row.get('Address'))
                dtype = (row.get('data_type') or row.get('Data Type') or 'uint16').strip().lower()
                self.points[name] = (address, dtype, self.count_of(dtype))
                default = row.get('default_value') or row.get('Default Value')
                if default not in (None, ''):
                    self.defaults[name] = self.parse(dtype, default)

    @staticmethod
    def count_of(dtype: str) -> int:
        m = re.match(r'string\[(\d+)\]', dtype)
        if m:
            return (int(m.group(1)) + 1) // 2
        return WIDTHS.get(dtype, 1)

    @staticmethod
    def parse(dtype: str, text: str):
        if dtype.startswith('string'):
            return text
        if dtype == 'float32':
            return float(text)
        return int(float(text))

    @staticmethod
    def to_words(dtype: str, value) -> list[int]:
        if dtype.startswith('string'):
            n = PointTable.count_of(dtype) * 2
            data = str(value).encode('latin-1')[:n].ljust(n, b'\0')
            return list(struct.unpack(f'>{n // 2}H', data))
        if dtype == 'float32':
            return list(struct.unpack('>2H', struct.pack('>f', float(value))))
        count = WIDTHS.get(dtype, 1)
        signed = dtype.startswith('int') or dtype == 'sunssf'
        raw = int(value) & ((1 << (16 * count)) - 1) if not signed else int(value) % (1 << (16 * count))
        return [(raw >> (16 * (count - 1 - i))) & 0xFFFF for i in range(count)]

    @staticmethod
    def from_words(dtype: str, words: list[int]):
        if dtype.startswith('string'):
            return struct.pack(f'>{len(words)}H', *words).rstrip(b'\0').decode('latin-1')
        if dtype == 'float32':
            return struct.unpack('>f', struct.pack('>2H', *words))[0]
        raw = 0
        for w in words:
            raw = (raw << 16) | w
        bits = 16 * len(words)
        if (dtype.startswith('int') or dtype == 'sunssf') and raw >= 1 << (bits - 1):
            raw -= 1 << bits
        return raw


class SunSpecDevice:
    def __init__(self, registry_csv: Path, host: str = '127.0.0.1', port: int = 0, unit_id: int = 1):
        from umodbus.server.tcp import RequestHandler, get_server
        self.table = PointTable(registry_csv)
        self.unit_id = unit_id
        self.words: dict[int, int] = {}
        self.written: list[tuple[str, object]] = []
        self._lock = threading.Lock()
        for name, value in self.table.defaults.items():
            self.set_point(name, value)
        for name, (address, dtype, count) in self.table.points.items():
            for a in range(address, address + count):
                self.words.setdefault(a, 0)
        ThreadingTCPServer.allow_reuse_address = True
        ThreadingTCPServer.daemon_threads = True
        self.server = get_server(ThreadingTCPServer, (host, port), RequestHandler)    # one connection per master: the driver and a demonstrator's client
        self.host, self.port = self.server.server_address[:2]
        words, lock, device = self.words, self._lock, self

        @self.server.route(slave_ids=[unit_id], function_codes=[3, 4], addresses=list(words))
        def read(slave_id, address, function_code):
            with lock:
                return words[address]

        @self.server.route(slave_ids=[unit_id], function_codes=[6, 16], addresses=list(words))
        def write(slave_id, address, value, function_code):
            with lock:
                words[address] = int(value) & 0xFFFF
            device._note_write(address)
        self._thread = threading.Thread(target=self.server.serve_forever, name='sunspec-device', daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def _note_write(self, address: int):
        for name, (start, dtype, count) in self.table.points.items():
            if start <= address < start + count:
                self.written.append((name, self.get_point(name)))

    def set_point(self, name: str, value):
        address, dtype, count = self.table.points[name]
        with self._lock:
            for i, w in enumerate(PointTable.to_words(dtype, value)):
                self.words[address + i] = w

    def get_point(self, name: str):
        address, dtype, count = self.table.points[name]
        with self._lock:
            return PointTable.from_words(dtype, [self.words[address + i] for i in range(count)])


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description='A SunSpec inverter as a Modbus TCP slave, holding a registry\'s default values.')
    p.add_argument('--registry', required=True, help='registry CSV (volttron_point_name, address, data_type, default_value, …)')
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=5020)
    p.add_argument('--unit', type=int, default=1)
    p.add_argument('--live', action=argparse.BooleanOptionalAction, default=True,
                   help='move the meter readings (701_W, 701_Var, 701_Hz, 701_VL1) along slow waves so telemetry paths show change; --no-live keeps the registry defaults')
    p.add_argument('--period', type=float, default=5.0, help='seconds between updates of the live readings')
    args = p.parse_args(argv)
    try:
        import umodbus  # noqa: F401
    except ImportError:
        print('the umodbus package is required: pip install "icmstack[devices]"', file=sys.stderr)
        return 2
    device = SunSpecDevice(Path(args.registry), args.host, args.port, args.unit).start()
    print(f'sunspec device: {device.host}:{device.port} unit {device.unit_id}, {len(device.table.points)} points from {args.registry}'
          + (f', live readings every {args.period} s' if args.live else ', static readings'), flush=True)
    import math
    live = [(name, base, swing, period) for name, base, swing, period in (('701_W', 4000, 600, 180), ('701_Var', 300, 80, 240), ('701_Hz', 6000, 3, 60), ('701_VL1', 2400, 20, 120))
            if name in device.table.points]      # raw register values; the scale factors in the registry apply (Hz x0.01, V x0.1)
    t = 0.0
    try:
        while True:
            if args.live and live:
                for name, base, swing, period in live:
                    device.set_point(name, int(round(base + swing * math.sin(2 * math.pi * t / period))))
                if int(t) % 60 == 0:
                    print('readings: ' + ', '.join(f'{name}={device.get_point(name)}' for name, *_ in live), flush=True)
                time.sleep(args.period); t += args.period
            else:
                time.sleep(3600)
    except KeyboardInterrupt:
        device.stop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
