# icmstack

One command line to install, start and stop the interoperability control framework (a VOLTTRON platform, its agents,
the configuration UI, a broker) and the dummy devices used to test and demonstrate it.

```
icmstack                      # menu: what is installed and running, pick a component or a preset
icmstack up [PRESET]          # install and start a preset (minimal, demo, showroom, matrix, ui-dev)
icmstack down                 # stop everything of the profile, reverse dependency order
icmstack status               # one line per component
icmstack start NAME…          # start components (and what they need, after asking)
icmstack stop NAME…
icmstack logs NAME [-f]
icmstack doctor               # what the machine has and lacks
icmstack config               # the recorded answers of the profile
```

Every input is a question with a default in brackets; Enter accepts it. The answer is recorded in the profile
(`~/.icmstack/<profile>/answers.toml`) and offered as the default next time. A question can be answered on the command
line (`--instance-name site1`) or in the environment (`ICMSTACK_INSTANCE_NAME`), in which case it is not asked; `--yes`
takes the default for whatever is still open. Secrets are never stored and are not accepted as plain options.

## What works today

The framework on modular VOLTTRON: `platform`, `web`, the agents (`driver`, `interop`, `resource-bridge`, `bus-adapter`,
`rtc`, `scheduler`), `ui` and `broker`, so `icmstack up minimal` brings up a platform with the UI from an empty
directory. Every dummy device: the pure-Python `sunspec-device`, `modbus-sim`, `openfmb-party` and `fake-device`, and
the container devices `dnp3-outstation` and `dnp3-master` (the IEEE 1815.2 test tool's reference binaries; set
`IEEE1815_2_TEST_TOOL` to its checkout and build it once so its backend image and cargo-cache volume exist) and
`sep2-server` and `sep2-client` (the GridAPPS-D Go implementations, images `sep2server:e2e` and `inverterclient:e2e`).
Each registers with the platform; the two served-role parties register the device the platform serves for them.
`icmstack up demo` wires the SunSpec device's readings to an OpenFMB profile on the broker; `up matrix` adds the
container devices in both roles; `up showroom` runs everything with nothing registered. `icmstack doctor` lists what
the machine has and lacks. The fastlib platform backend is not available yet and says so.

## Installing

```bash
pip install -e path/to/icm-stack            # the tool alone
pip install -e 'path/to/icm-stack[devices]' # with the pure-Python dummy devices (uModbus, paho-mqtt, protobuf)
icmstack doctor                             # what this machine has and lacks
```

Run the tests with `pytest` after `pip install -e '.[dev]'`.

### Agents from checkouts

`--agent-source checkout` (or the recorded answer) installs the agents from your working copies instead of PyPI:
`VOLTTRON_MODULAR_DRIVERS` names the modular drivers tree (the proxies are expected beside it) and `ICM_MODULES` the
directory holding the interoperability repositories, `icm-utils` among them. The shared libraries the agents import
(`icm-lib-transforms`, `icm-lib-resources` and the `icm-lib-models-*` packages) are then installed editable from
`icm-utils` and recorded as path dependencies of the platform's own poetry project, so the platform can resolve the
agents' requirements on them before any release exists. The project mirrors the environment: when the two differ (a
platform home created earlier, a switch to another venv, upgraded packages) the project is written again on the next
start and re-locked. The installed state is per environment too, so after a venv switch, or a venv deleted and created
again at the same path, `up` installs everything into the new one; the web admin user already in the platform's home is
kept, with its password, unless a password is chosen. To start over completely: `icmstack down`, then delete the
profile directory (`~/.icmstack/<profile>`, which holds VOLTTRON_HOME, certificates, logs and the recorded answers) and
the venv.
