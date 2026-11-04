"""The GridAPPS-D Go IEEE 2030.5 implementations as independent parties: ``sep2server`` as the utility server the
platform's DER client talks to, ``inverterclient`` as the DER client that registers with the server the platform
serves. Both run in docker on the host network; certificates come from the server image's ``certs`` commands (one CA
for every party). From the real-platform e2e harness and the 2030.5 driver's e2e_sep2server module."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

SERVER_IMAGE = os.environ.get('SEP2SERVER_IMAGE', 'sep2server:e2e')
CLIENT_IMAGE = os.environ.get('SEP2CLIENT_IMAGE', 'inverterclient:e2e')
ADMIN_KEY = 'icmstack-admin-key'
PEN = '40732'


def images_present() -> dict[str, bool]:
    return {image: subprocess.run(['docker', 'image', 'inspect', image], capture_output=True).returncode == 0 for image in (SERVER_IMAGE, CLIENT_IMAGE)}


# ---------------------------------------------------------------- identities (IEEE 2030.5 section 6.3.4)
def certificate_der(path: str | Path) -> bytes:
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
    except ImportError:
        raise RuntimeError('the cryptography package is missing in icmstack\'s environment (needed to read the 2030.5 certificates): '
                           'pip install cryptography, or pip install "icmstack[devices]"') from None
    data = Path(path).read_bytes()
    cert = x509.load_pem_x509_certificate(data) if b'-----BEGIN' in data else x509.load_der_x509_certificate(data)
    return cert.public_bytes(serialization.Encoding.DER)


def lfdi_from_cert(path: str | Path) -> str:
    """The SHA-256 fingerprint of the DER certificate, left-truncated to 160 bits: 40 upper-case hex digits."""
    return hashlib.sha256(certificate_der(path)).digest()[:20].hex().upper()


def check_digit(digits: str) -> int:
    return (10 - sum(int(d) for d in digits) % 10) % 10


def sfdi_from_lfdi(lfdi: str) -> int:
    """The fingerprint's first 36 bits as 11 decimal digits plus a check digit."""
    digits = f'{int(lfdi[:9], 16):011d}'
    return int(digits + str(check_digit(digits)))


# ---------------------------------------------------------------- certificates
def _run_certs(cert_dir: Path, *args: str) -> str:
    user = f'{os.getuid()}:{os.getgid()}'
    result = subprocess.run(['docker', 'run', '--rm', '-u', user, '-e', 'HOME=/tmp', '-v', f'{cert_dir}:/tls', SERVER_IMAGE, 'certs', *args],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f'certs {args[0]} failed: {result.stdout} {result.stderr}')
    return result.stdout


def generate_certs(cert_dir: Path) -> dict[str, Path]:
    """One CA; a server certificate for 127.0.0.1 (the Go server, and the server the platform serves); a device
    certificate for the platform's DER client; one for the Go client; a receiver certificate for notifications."""
    cert_dir.mkdir(parents=True, exist_ok=True)
    cert_dir.chmod(0o755)
    paths = certificate_paths(cert_dir)
    if not all(p.exists() for p in paths.values()):
        _run_certs(cert_dir, 'generate-ca', '-out', '/tls')
        _run_certs(cert_dir, 'generate-server', '-out', '/tls', '-ca', '/tls/ca.crt', '-ca-key', '/tls/ca.key', '-hosts', '127.0.0.1,localhost')
        _run_certs(cert_dir, 'generate-device', '-out', '/tls', '-ca', '/tls/ca.crt', '-ca-key', '/tls/ca.key', '-name', 'volttron',
                   '-hw-serial', 'VT0001', '-hw-type', f'1.3.6.1.4.1.{PEN}.1')
        _run_certs(cert_dir, 'generate-device', '-out', '/tls', '-ca', '/tls/ca.crt', '-ca-key', '/tls/ca.key', '-name', 'goclient',
                   '-hw-serial', 'GO0001', '-hw-type', f'1.3.6.1.4.1.{PEN}.1')
        (cert_dir / 'notify').mkdir(exist_ok=True)
        _run_certs(cert_dir, 'generate-server', '-out', '/tls/notify', '-ca', '/tls/ca.crt', '-ca-key', '/tls/ca.key',
                   '-hosts', '127.0.0.1,localhost', '-cn', 'icmstack notify')
        for key in cert_dir.rglob('*.key'):
            key.chmod(0o644)        # the containers run as this user; the platform's proxy reads the device key
    return paths


def certificate_paths(cert_dir: Path) -> dict[str, Path]:
    return {'ca': cert_dir / 'ca.crt', 'server_cert': cert_dir / 'server.crt', 'server_key': cert_dir / 'server.key',
            'device_cert': cert_dir / 'volttron.crt', 'device_key': cert_dir / 'volttron.key',
            'goclient_cert': cert_dir / 'goclient.crt', 'goclient_key': cert_dir / 'goclient.key',
            'notify_cert': cert_dir / 'notify' / 'server.crt', 'notify_key': cert_dir / 'notify' / 'server.key'}


def write_fixture(path: Path, lfdi: str, sfdi: int) -> None:
    """One EndDevice (the platform's client), one FSA, one program with a DefaultDERControl."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'''end_devices:
  - id: "1"
    sfdi: "{sfdi:012d}"
    lfdi: "{lfdi}"
    enabled: true
    changed_time: 100
    registration_link: "/edev/1/rg"
    function_set_assignments_list_link:
      href: "/edev/1/fsa"
      all: 1
    der_list_link:
      href: "/edev/1/der"
      all: 1
fsas:
  - end_device_id: "1"
    id: "0"
    der_program_list_link:
      href: "/edev/1/fsa/0/derp"
      all: 1
der_programs:
  - end_device_id: "1"
    fsa_id: "0"
    id: p1
    mrid: "D2D2D2D2D2D2D2D2"
    description: "icmstack program"
    primacy: 0
    default_der_control_link: "/edev/1/fsa/0/derp/p1/dderc"
    der_control_list_link:
      href: "/edev/1/fsa/0/derp/p1/derc"
      all: 0
default_der_controls:
  - end_device_id: "1"
    fsa_id: "0"
    der_program_id: p1
    der_control_base:
      op_mod_connect: true
      op_mod_max_lim_w: 10000
''')
    path.chmod(0o644)
    path.parent.chmod(0o755)


# ---------------------------------------------------------------- containers
def _docker(cmd: list[str]) -> None:
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError(f'{" ".join(cmd[:3])} failed: {done.stderr.strip()}')


def container_running(name: str) -> bool:
    done = subprocess.run(['docker', 'inspect', '-f', '{{.State.Running}}', name], capture_output=True, text=True)
    return done.returncode == 0 and done.stdout.strip() == 'true'


def container_logs(name: str, lines: int = 40) -> str:
    done = subprocess.run(['docker', 'logs', '--tail', str(lines), name], capture_output=True, text=True)
    return done.stdout + done.stderr


def container_stop(name: str) -> bool:
    existed = subprocess.run(['docker', 'inspect', name], capture_output=True).returncode == 0
    subprocess.run(['docker', 'rm', '-f', name], capture_output=True)
    return existed


def start_server(name: str, cert_dir: Path, fixture: Path, port: int, admin_port: int, capture_dir: Path | None = None, timeout: float = 30.0) -> None:
    """The Go server on 127.0.0.1:port, admin API on admin_port, seeded with the fixture; SSL_CERT_FILE makes its
    notifier trust the CA that signed the platform's receiver certificate."""
    subprocess.run(['docker', 'rm', '-f', name], capture_output=True)
    user = f'{os.getuid()}:{os.getgid()}'
    cmd = ['docker', 'run', '-d', '--name', name, '--network', 'host', '-u', user, '-e', 'HOME=/tmp', '-e', 'SSL_CERT_FILE=/tls/ca.crt',
           '-v', f'{cert_dir}:/tls:ro', '-v', f'{fixture.parent}:/fix:ro', '-e', 'SEP2_CERT_DIR=/tls', '-e', f'SEP2_ADDR=127.0.0.1:{port}',
           '-e', f'SEP2_ADMIN_LISTEN=127.0.0.1:{admin_port}', '-e', f'SEP2_ADMIN_KEY={ADMIN_KEY}', '-e', f'SEP2_PEN={PEN}',
           '-e', 'SEP2_NOTIFICATION_ALLOW_LOOPBACK=true', '-e', f'SEP2_BOOT_FIXTURE=/fix/{fixture.name}']
    if capture_dir is not None:
        # The server's traffic capture: every request it serves is recorded, the only outside view of the readings it accepts.
        capture_dir.mkdir(parents=True, exist_ok=True); capture_dir.chmod(0o777)
        cmd += ['-v', f'{capture_dir}:/cap', '-e', 'SEP2_TRAFFIC_CAPTURE=true', '-e', 'SEP2_TRAFFIC_DIR=/cap']
    _docker(cmd + [SERVER_IMAGE, 'serve'])
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            admin(admin_port, 'GET', '/api/der/controls?device=1')
            return
        except Exception:
            time.sleep(0.3)
    raise RuntimeError(f'sep2server did not come up:\n{container_logs(name)}')


def admin(admin_port: int, method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f'http://127.0.0.1:{admin_port}{path}', data=data, method=method,
                                     headers={'Authorization': f'Bearer {ADMIN_KEY}', 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=10) as response:
        text = response.read().decode()
    return json.loads(text) if text else {}


def create_control(admin_port: int, max_lim_w: int, duration: int = 120) -> str:
    """An immediate DERControl limiting active power, through the server's admin API; returns its mRID."""
    body = {'derProgramHref': '/edev/1/fsa/0/derp/p1', 'type': 'maxLimW', 'startTime': int(time.time()), 'durationSeconds': max(60, duration),
            'description': 'icmstack', 'maxLimW': max_lim_w}
    return admin(admin_port, 'POST', '/api/der/controls', body)['mRID']


def start_client(name: str, cert_dir: Path, server_url: str, pin: int, notify_port: int, timescale: int = 30, report_interval: int = 5) -> None:
    """The Go inverterclient (a DER client with a built-in PV simulator) against the server the platform serves."""
    subprocess.run(['docker', 'rm', '-f', name], capture_output=True)
    _docker(['docker', 'run', '-d', '--name', name, '--network', 'host', '-u', f'{os.getuid()}:{os.getgid()}', '-v', f'{cert_dir}:/tls:ro', CLIENT_IMAGE,
             '--server', server_url, '--cert', '/tls/goclient.crt', '--key', '/tls/goclient.key', '--ca', '/tls/ca.crt', '--csip', '--pin', str(pin),
             '--scenario', 'normal', '--timescale', str(timescale), '--tick', '1s', '--report-interval', f'{report_interval}s',
             '--notify-listen', f'127.0.0.1:{notify_port}'])
