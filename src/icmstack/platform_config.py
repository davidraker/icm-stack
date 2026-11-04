"""Reading and writing the platform's configuration store through vctl, and the entries a registered device needs."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .components.base import Context
from .components.platform_modular import vctl


def list_entries(ctx: Context, identity: str) -> list[str]:
    done = vctl(ctx, 'config', 'list', identity, check=False, timeout=60)
    return [l.strip() for l in done.stdout.splitlines() if l.strip()] if done.returncode == 0 else []


def get_json(ctx: Context, identity: str, name: str) -> dict | None:
    if name not in list_entries(ctx, identity):      # asking the store for a missing entry logs an error in the platform
        return None
    done = vctl(ctx, 'config', 'get', identity, name, check=False, timeout=60)
    if done.returncode != 0:
        return None
    try:
        return json.loads(done.stdout)
    except ValueError:
        return None


def store_json(ctx: Context, identity: str, name: str, data: dict) -> None:
    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
        json.dump(data, f, indent=2)
    try:
        vctl(ctx, 'config', 'store', identity, name, f.name, timeout=120)
    finally:
        Path(f.name).unlink(missing_ok=True)


def store_csv(ctx: Context, identity: str, name: str, path: str | Path) -> None:
    vctl(ctx, 'config', 'store', identity, name, str(path), '--csv', timeout=120)


def device_entry(driver_type: str, registry_name: str, remote: dict, interval: float = 2.0) -> dict:
    """A platform-driver device entry in the shape the e2e harness uses: the remote config carries the driver type."""
    return {'driver_type': driver_type, 'remote_config': {'driver_type': driver_type, **remote},
            'registry_config': f'config://registry_configs/{registry_name}', 'interval': interval,
            'publish_depth_first_all': True, 'publish_depth_first_multi': True, 'timezone': 'UTC'}


def merge_service_config(ctx: Context, identity: str, formats: dict | None = None, transforms: list | None = None,
                         mappings: list | None = None) -> dict:
    """Add formats, transforms and mappings to the interoperability service's config entry (created when absent); a
    mapping with the same UAI, a transform with the same input and output, or a format with the same name is replaced."""
    config = get_json(ctx, identity, 'config') or {}
    config.setdefault('formats', {}).update(formats or {})
    existing = [t for t in config.get('transforms', []) if not any(t.get('input_format') == n.get('input_format') and t.get('output_format') == n.get('output_format') for n in transforms or [])]
    config['transforms'] = existing + list(transforms or [])
    topics = {'/'.join(m.get('uai', [])) for m in mappings or []}
    config['mappings'] = [m for m in config.get('mappings', []) if '/'.join(m.get('uai', [])) not in topics] + list(mappings or [])
    store_json(ctx, identity, 'config', config)
    return config


def canonical_mapping(topic: str, data_format: str, owner: str = 'platform.driver') -> dict:
    segments = [s for s in topic.split('/') if s]
    return {'uai': segments, 'resource_type': 'canonical',
            'resource': {'data_format': data_format, 'owner': owner, 'publication_topic': f'devices/{topic}/all', 'rpc_topic': f'devices/{topic}'}}
