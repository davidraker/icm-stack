"""A plain Modbus TCP device with known values (pymodbus), the simulator of the Modbus driver's tests.
Runnable: ``python -m icmstack.devices.modbus_sim --port 5021``. The matching registry is data/registries/modbus_sim.modbus.csv."""
from __future__ import annotations

import argparse
import asyncio
import sys


def build_context():
    from pymodbus.client.mixin import ModbusClientMixin as M
    from pymodbus.datastore import ModbusDeviceContext, ModbusSequentialDataBlock, ModbusServerContext
    D = M.DATATYPE
    hr, ir = [0] * 1300, [0] * 1300          # addresses 0-1299 exist; anything above is an illegal address
    co, di = [False] * 100, [False] * 100

    def put(block, addr, regs):
        block[addr:addr + len(regs)] = regs
    put(ir, 1001, M.convert_to_registers(412.5, D.FLOAT32))          # ReturnAirCO2 (input)
    put(ir, 1010, M.convert_to_registers(-7, D.INT16))                # Temp (input)
    put(hr, 1003, M.convert_to_registers(1000.0, D.FLOAT32))         # CO2Stpt (holding)
    put(hr, 1005, M.convert_to_registers('RTU-7', D.STRING) + [0])   # Name (holding, 3 registers)
    put(hr, 1010, [3])                                                # Mode (holding)
    di[3] = True                                                      # FanStatus
    co[5] = False                                                     # FanCmd
    ctx = ModbusDeviceContext(hr=ModbusSequentialDataBlock(1, hr), ir=ModbusSequentialDataBlock(1, ir),
                              co=ModbusSequentialDataBlock(1, co), di=ModbusSequentialDataBlock(1, di))
    return ModbusServerContext(devices=ctx, single=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=5021)
    args = p.parse_args(argv)
    try:
        from pymodbus.server import StartAsyncTcpServer
    except ImportError:
        print('the pymodbus package is required: pip install pymodbus', file=sys.stderr)
        return 2
    print(f'modbus simulator: {args.host}:{args.port}, unit 1, points of data/registries/modbus_sim.modbus.csv', flush=True)
    try:
        asyncio.run(StartAsyncTcpServer(build_context(), address=(args.host, args.port)))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
