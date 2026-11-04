from .agents import agent_components
from .base import Component, Context, NotReady, Registry, Status
from .broker import Broker
from .planned import DEVICES
from .platform_modular import Platform, Web
from .ui import UI
from .devices.sunspec import SunSpecDevice
from .devices.simple import FakeDevice, ModbusSim, OpenFmbParty
from .devices.containers import Dnp3Master, Dnp3Outstation, Sep2Client, Sep2Server


def default_registry() -> Registry:
    return Registry([Platform(), Web(), *agent_components(), UI(), Broker(), SunSpecDevice(), ModbusSim(), OpenFmbParty(), FakeDevice(), Dnp3Outstation(), Dnp3Master(), Sep2Server(), Sep2Client()])


__all__ = ['Component', 'Context', 'NotReady', 'Registry', 'Status', 'default_registry']
