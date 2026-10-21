"""Strict, stdlib-only component registry; loading never starts services or reads secrets."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field, fields
import math
from pathlib import Path, PureWindowsPath
import re
import tomllib
from urllib.parse import parse_qsl, urlsplit


class RegistryError(ValueError):
    """Invalid registry configuration or unknown component."""


@dataclass(frozen=True)
class Probe:
    type: str
    path: str = "/"
    token_env: str | None = None
    header: str | None = None
    expect_status: int | None = None
    expect_body: dict[str, str | int | float | bool | None] | None = None
    host: str | None = None
    port: int | None = None
    timeout_seconds: float = 2
    max_response_bytes: int = 65536


@dataclass(frozen=True)
class Connection:
    host_env: str | None = None
    port_env: str | None = None
    database_env: str | None = None
    user_env: str | None = None
    schema_env: str | None = None
    graph_env: str | None = None
    password_env: str | None = None
    database: str | None = None
    user: str | None = None
    schema: str | None = None
    graph: str | None = None


@dataclass(frozen=True)
class Dependency:
    name: str
    required: bool = True
    connection: Connection | None = None
    endpoint_env: str | None = None


@dataclass(frozen=True)
class Component:
    name: str
    kind: str
    mode: str
    provisioning: str
    enabled: bool = True
    owner: str | None = None
    engine: str | None = None
    default_stack: bool = False
    endpoint: str | None = None
    host: str | None = None
    port: int | None = None
    network_host: str | None = None
    network_port: int | None = None
    compose: str | None = None
    compose_project: str | None = None
    expected_containers: int = 1
    data_dir: str | None = None
    probe: Probe | None = None
    requires: dict[str, Dependency] = field(default_factory=dict)
    start_command: tuple[str, ...] = ()
    install_command: tuple[str, ...] = ()
    dev_install_command: tuple[str, ...] = ()
    compose_command: tuple[str, ...] = ()
    process_markers: tuple[str, ...] = ()
    env: dict[str, str | int | bool] = field(default_factory=dict)
    secret_env: dict[str, str] = field(default_factory=dict)
    host_env: str | None = None
    port_env: str | None = None
    endpoint_env: str | None = None
    bind_host: str | None = None
    shared_network: bool = False
    network: str = "icarus"
    workdir: str | None = None
    image_action: str = "build"
    endpoint_args: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Registry:
    version: int
    components: dict[str, Component]

    def get(self, name: str) -> Component:
        try:
            return self.components[name]
        except KeyError:
            raise RegistryError(f"unknown component: {name}") from None

    def names(self) -> tuple[str, ...]:
        return tuple(self.components)

    def default_stack(self) -> tuple[str, ...]:
        return tuple(name for name, component in self.components.items()
                     if component.enabled and component.default_stack)


_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")
_COMMAND_FIELDS = ("start_command", "install_command", "dev_install_command",
                   "compose_command", "process_markers")


def _table(value: object, context: str) -> dict:
    if not isinstance(value, Mapping) or any(not isinstance(k, str) for k in value):
        raise RegistryError(f"{context} must be a table with string keys")
    return dict(value)


def _keys(data: dict, allowed: set[str], context: str) -> None:
    unknown = data.keys() - allowed
    if unknown:
        raise RegistryError(f"{context}: unknown fields: {', '.join(sorted(unknown))}")


def _model_keys(data: dict, model: type, context: str) -> None:
    _keys(data, {f.name for f in fields(model)} - {"name"}, context)


def _string(value: object, context: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()) or "\x00" in value:
        raise RegistryError(f"{context} must be {'a' if empty else 'a nonempty'} string")
    return value


def _env_name(value: object, context: str) -> str:
    value = _string(value, context)
    if not _ENV_NAME.fullmatch(value):
        raise RegistryError(f"{context} must be an environment variable name")
    return value


def _integer(value: object, context: str, minimum: int, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise RegistryError(f"{context} must be an integer in the permitted range")
    return value


def _booleans(data: dict, names: tuple[str, ...], context: str) -> None:
    for name in names:
        if name in data and type(data[name]) is not bool:
            raise RegistryError(f"{context}.{name} must be a bool")


def _strings(data: dict, names: tuple[str, ...], context: str) -> None:
    for name in names:
        if name in data:
            _string(data[name], f"{context}.{name}")


def _env_refs(data: dict, context: str) -> None:
    for name, value in data.items():
        if name.endswith("_env"):
            _env_name(value, f"{context}.{name}")


def _choice(data: dict, name: str, choices: set[str], context: str) -> None:
    if not isinstance(data.get(name), str) or data[name] not in choices:
        raise RegistryError(f"{context}.{name} must be one of {', '.join(sorted(choices))}")


def _secret_name(name: str) -> bool:
    normalized = name.upper().replace("-", "_")
    parts = set(normalized.split("_"))
    return bool(parts & {"PASSWORD", "PASSWD", "PASS", "TOKEN", "SECRET", "CREDENTIAL", "CREDENTIALS"}
                or normalized in {"APIKEY", "PRIVATEKEY", "ACCESSKEY", "PGPASSWORD", "MYSQL_PWD", "REDISCLI_AUTH"}
                or any(key in normalized for key in ("API_KEY", "PRIVATE_KEY", "ACCESS_KEY")))


def _endpoint(value: object, context: str) -> None:
    value = _string(value, context)
    try:
        url = urlsplit(value)
        port = url.port
        invalid = (url.scheme not in {"http", "https", "ws", "wss"} or not url.hostname
                   or url.username is not None or url.password is not None
                   or any(c.isspace() or ord(c) < 32 for c in value) or "\\" in value
                   or (port is not None and not 1 <= port <= 65535)
                   or any(_secret_name(key) for key, _ in parse_qsl(url.query)))
    except ValueError:
        invalid = True
    if invalid:
        raise RegistryError(f"{context} must be an absolute HTTP/WebSocket URL without credentials")


def _relative_path(value: object, root: Path, context: str) -> None:
    value = _string(value, context)
    path = Path(value)
    if (path.is_absolute() or PureWindowsPath(value).drive or "\\" in value
            or ".." in path.parts):
        raise RegistryError(f"{context} must be a repository-relative path without traversal")
    try:
        (root / path).resolve().relative_to(root)
    except (ValueError, OSError, RuntimeError):
        raise RegistryError(f"{context} must not escape the repository through symlinks") from None


def _probe(raw: object, context: str) -> Probe:
    data = _table(raw, context)
    _model_keys(data, Probe, context)
    _choice(data, "type", {"http", "tcp"}, context)
    _strings(data, ("path", "host", "header"), context)
    _env_refs(data, context)
    if "port" in data:
        _integer(data["port"], f"{context}.port", 1, 65535)
    path = data.get("path", "/")
    if not path.startswith("/") or path.startswith("//") or "\\" in path or any(ord(c) < 32 for c in path):
        raise RegistryError(f"{context}.path must be an absolute URL path, not a URL")
    if "header" in data and not _HEADER_NAME.fullmatch(data["header"]):
        raise RegistryError(f"{context}.header must be an HTTP header name")
    if "expect_status" in data:
        _integer(data["expect_status"], f"{context}.expect_status", 100, 599)
    if "expect_body" in data:
        body_context = f"{context}.expect_body"
        body = _table(data["expect_body"], body_context)
        for key, value in body.items():
            if _secret_name(key):
                raise RegistryError(f"{body_context}: secret literals are forbidden")
            if (type(value) not in (str, int, float, bool, type(None))
                    or (type(value) is float and not math.isfinite(value))):
                raise RegistryError(f"{body_context} must map JSON field names to scalar JSON values")
        data["expect_body"] = body
    timeout = data.get("timeout_seconds", 2)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise RegistryError(f"{context}.timeout_seconds must be a finite positive number")
    if "max_response_bytes" in data:
        _integer(data["max_response_bytes"], f"{context}.max_response_bytes", 1)
    return Probe(**data)


def _dependency(name: str, raw: object, context: str) -> Dependency:
    data = _table(raw, context)
    _model_keys(data, Dependency, context)
    _booleans(data, ("required",), context)
    _env_refs(data, context)
    if "connection" in data:
        connection = _table(data["connection"], f"{context}.connection")
        _model_keys(connection, Connection, f"{context}.connection")
        _strings(connection, ("database", "user", "schema", "graph"), f"{context}.connection")
        _env_refs(connection, f"{context}.connection")
        data["connection"] = Connection(**connection)
    return Dependency(name=name, **data)


def _component(name: str, raw: object, defaults: dict, root: Path) -> Component:
    context = f"components.{name}"
    data = _table(raw, context)
    _model_keys(data, Component, context)
    data = {"mode": "local", "default_stack": False, **defaults, **data}
    _choice(data, "kind", {"app", "middleware"}, context)
    _choice(data, "mode", {"local", "remote"}, context)
    _choice(data, "provisioning", {"compose", "process", "embedded", "foreground", "bundled", "none"}, context)
    _booleans(data, ("enabled", "default_stack", "shared_network"), context)
    _strings(data, ("owner", "engine", "host", "network_host", "compose_project", "bind_host", "network"), context)
    # secret_env is a mapping, not a scalar *_env reference.
    _env_refs({k: v for k, v in data.items() if k != "secret_env"}, context)
    for key in ("port", "network_port"):
        if key in data:
            _integer(data[key], f"{context}.{key}", 1, 65535)
    if "expected_containers" in data:
        _integer(data["expected_containers"], f"{context}.expected_containers", 1)
    if "endpoint" in data:
        _endpoint(data["endpoint"], f"{context}.endpoint")
    if "image_action" in data:
        _choice(data, "image_action", {"build", "pull"}, context)
    for key in ("compose", "data_dir", "workdir"):
        if key in data:
            _relative_path(data[key], root, f"{context}.{key}")
    for key in _COMMAND_FIELDS:
        if key in data:
            if not isinstance(data[key], (list, tuple)):
                raise RegistryError(f"{context}.{key} must be an argv array")
            data[key] = tuple(_string(arg, f"{context}.{key}") for arg in data[key])
    for key in ("env", "secret_env"):
        if key not in data:
            continue
        mapping = _table(data[key], f"{context}.{key}")
        for destination, value in mapping.items():
            _env_name(destination, f"{context}.{key} destination")
            if key == "secret_env":
                _env_name(value, f"{context}.secret_env.{destination}")
            elif _secret_name(destination):
                raise RegistryError(f"{context}.env.{destination}: secret literals are forbidden; use secret_env")
            elif type(value) not in (str, int, bool):
                raise RegistryError(f"{context}.env.{destination} must be a TOML string, int or bool")
            elif isinstance(value, str):
                _string(value, f"{context}.env.{destination}", empty=True)
        data[key] = mapping
    if "probe" in data:
        data["probe"] = _probe(data["probe"], f"{context}.probe")
    requires = _table(data.get("requires", {}), f"{context}.requires")
    data["requires"] = {dependency: _dependency(dependency, spec, f"{context}.requires.{dependency}")
                        for dependency, spec in requires.items()}
    if "endpoint_args" in data:
        bindings = _table(data["endpoint_args"], f"{context}.endpoint_args")
        if data["provisioning"] != "foreground":
            raise RegistryError(f"{context}.endpoint_args requires foreground provisioning")
        for flag, dependency in bindings.items():
            if not re.fullmatch(r"--[a-zA-Z][a-zA-Z0-9-]*", flag) or dependency not in data["requires"]:
                raise RegistryError(f"{context}.endpoint_args must bind long flags to declared dependencies")
        data["endpoint_args"] = bindings
    data.setdefault("compose_project", f"icarus-{name}")
    component = Component(name=name, **data)
    if component.mode == "local" and component.provisioning == "compose" and component.compose is None:
        raise RegistryError(f"{context}.compose is required for local compose provisioning")
    if component.provisioning in {"embedded", "bundled"} and component.owner is None:
        raise RegistryError(f"{context}.owner is required for hosted provisioning")
    probe = component.probe
    if component.kind == "middleware" and not (component.host and component.port):
        if not (component.provisioning == "bundled" and probe and probe.host and probe.port):
            raise RegistryError(f"{context} middleware requires host and port")
    if probe and probe.type == "http":
        if component.endpoint is None:
            raise RegistryError(f"{context}.endpoint is required for an HTTP probe")
        if urlsplit(component.endpoint).scheme not in {"http", "https"}:
            raise RegistryError(f"{context}.endpoint must use HTTP(S) for an HTTP probe")
    if probe and probe.type == "tcp" and not ((probe.host or component.host) and (probe.port or component.port)):
        raise RegistryError(f"{context}.probe requires host and port for TCP")
    return component


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge tables, replacing scalars and arrays, without aliasing inputs."""
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _read_config(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        # Decode errors can include source text on some Python versions.
        raise RegistryError(f"failed to parse {path}: invalid TOML") from None
    except (OSError, UnicodeError):
        raise RegistryError(f"failed to read {path}") from None


def load_registry(
    repo_root: Path,
    *,
    overrides: Mapping[str, Mapping[str, object]] | None = None,
    config_path: Path | str | None = None,
) -> Registry:
    """Load base → sibling <stem>.local.toml → component overrides.

    Relative config paths are relative to repo_root, never the caller's CWD.
    Metadata paths remain repository-relative strings; argv is not executed or resolved.
    Validation applies to the effective merged configuration; graph cycles are Task 2.
    """
    root = Path(repo_root).resolve()
    base_path = Path(config_path) if config_path is not None else Path("icarus.toml")
    if not base_path.is_absolute():
        base_path = root / base_path
    data = _read_config(base_path)
    overlay_path = base_path.with_name(f"{base_path.stem}.local.toml")
    if overlay_path.exists():
        data = _deep_merge(data, _read_config(overlay_path))
    _keys(data, {"version", "defaults", "components"}, "registry")
    if type(data.get("version")) is not int or data["version"] != 1:
        raise RegistryError("registry.version must be 1")
    defaults = _table(data.get("defaults", {}), "defaults")
    _keys(defaults, {"mode", "default_stack"}, "defaults")
    if "mode" in defaults:
        _choice(defaults, "mode", {"local", "remote"}, "defaults")
    _booleans(defaults, ("default_stack",), "defaults")
    raw_components = _table(data.get("components"), "components")
    if not raw_components:
        raise RegistryError("components must not be empty")
    if overrides is not None:
        for name, override in _table(overrides, "overrides").items():
            if name not in raw_components:
                raise RegistryError(f"overrides: unknown component: {name}")
            raw_components[name] = _deep_merge(
                _table(raw_components[name], f"components.{name}"),
                _table(override, f"overrides.{name}"),
            )
    components = {name: _component(name, spec, defaults, root) for name, spec in raw_components.items()}
    for name, component in components.items():
        if component.owner is not None:
            if component.owner == name or component.owner not in components:
                raise RegistryError(f"components.{name}.owner must refer to another existing component")
        for dependency in component.requires:
            if dependency == name:
                raise RegistryError(f"components.{name} forbids self dependency")
            if dependency not in components:
                raise RegistryError(f"components.{name}: unknown dependency: {dependency}")
    return Registry(version=1, components=components)
