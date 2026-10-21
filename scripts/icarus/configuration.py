"""Configuration diagnostics and address normalization; no environment/secret I/O."""
from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from graph import topological_order
from registry import Registry, RegistryError, load_registry


def load_configuration(repo_root: Path, config_path=None, overrides=None) -> Registry:
    registry = load_registry(repo_root, config_path=config_path, overrides=overrides)
    components = {}
    for name, component in registry.components.items():
        if component.endpoint:
            url = urlsplit(component.endpoint)
            port = url.port or (443 if url.scheme in {"https", "wss"} else 80)
            if component.host not in (None, url.hostname) or component.port not in (None, port):
                raise RegistryError(f"{name}: endpoint contradicts host/port; use one address source")
            component = replace(component, host=url.hostname, port=port)
        components[name] = component
    registry = replace(registry, components=components)
    # Validate the complete enabled graph before any operation can mutate state.
    topological_order(registry, [c.name for c in components.values() if c.enabled])
    return registry


def address_override(component, values):
    """Keep URL and listener fields consistent for invocation-only overrides."""
    result = dict(values)
    if "endpoint" in values:
        try:
            url = urlsplit(values["endpoint"])
            host = url.hostname
            port = url.port or (443 if url.scheme in {"https", "wss"} else 80)
        except ValueError:
            raise RegistryError("endpoint override must be a valid absolute URL") from None
        if not host:
            raise RegistryError("endpoint override must be a valid absolute URL")
        result.setdefault("host", host)
        result.setdefault("port", port)
    elif component.endpoint and ("host" in values or "port" in values):
        url = urlsplit(component.endpoint)
        host = values.get("host", component.host or url.hostname)
        port = values.get("port", component.port or url.port)
        host = f"[{host}]" if ":" in host else host
        netloc = f"{host}:{port}" if port else host
        result["endpoint"] = urlunsplit(url._replace(netloc=netloc))
    return result


def configuration_document(registry: Registry, repo_root: Path, config_path=None, name=None):
    base = Path(config_path or "icarus.toml")
    if not base.is_absolute():
        base = repo_root / base
    base = base.resolve()
    local = base.with_name(f"{base.stem}.local.toml")
    components = [registry.get(name)] if name else registry.components.values()
    # Only strict registry metadata is serialized: dotenv/process values are never read.
    return {"version": registry.version, "sources": [str(base), *([str(local)] if local.exists() else [])],
            "components": {c.name: asdict(c) for c in components}}
