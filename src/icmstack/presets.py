"""Named sets of components."""
FRAMEWORK_CORE = ['platform', 'web', 'driver', 'interop', 'ui']
PRESETS: dict[str, dict] = {
    'minimal': {'components': FRAMEWORK_CORE + ['fake-device'], 'register': True,
                'description': 'platform, web, driver, interoperability service, UI, one fake device registered'},
    'demo': {'components': FRAMEWORK_CORE + ['resource-bridge', 'bus-adapter', 'broker', 'sunspec-device', 'openfmb-party'], 'register': True, 'wire': ['sunspec-to-openfmb', 'openfmb-to-sunspec'],
             'optional': ['rtc', 'scheduler'],
             'description': 'minimal plus Resource Bridge, bus adapter, broker, a SunSpec device and an OpenFMB party, registered and bridged'},
    'showroom': {'components': FRAMEWORK_CORE + ['resource-bridge', 'bus-adapter', 'broker', 'sunspec-device', 'dnp3-outstation', 'sep2-server'],
                 'register': False, 'optional': ['rtc', 'scheduler'],
                 'description': 'everything running, nothing registered: the configuration is the demonstration'},
    'matrix': {'components': FRAMEWORK_CORE + ['resource-bridge', 'bus-adapter', 'broker', 'sunspec-device', 'openfmb-party',
                                               'dnp3-outstation', 'dnp3-master', 'sep2-server', 'sep2-client'], 'register': True,
               'wire': 'all', 'description': 'demo plus the container devices in both roles, with every showcase path wired (icmstack wire --list)'},
    'ui-dev': {'components': ['ui'], 'register': False, 'fake_platform': True,
               'description': 'the UI against its in-process fake platform, no VOLTTRON'},
}
