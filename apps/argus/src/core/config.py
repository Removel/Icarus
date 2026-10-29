"""Validated application settings; secrets stay outside JSON configuration."""

import json
import os
import ipaddress
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from packages.runtime_environment import load_icarus_environment

PositiveSeconds = Annotated[float, Field(gt=0, allow_inf_nan=False)]
DEFAULT_SETTINGS_PATH = Path(__file__).resolve().parents[2] / 'settings.json'


class SettingsModel(BaseModel):
    model_config = ConfigDict(extra='forbid', validate_default=True, hide_input_in_errors=True)


class QQHistoryConfig(SettingsModel):
    enabled: bool = False
    endpoint: str | None = None
    token_env: str = 'SNOWLUMA_MCP_TOKEN'
    timeout_seconds: PositiveSeconds = 2.0

    @model_validator(mode='after')
    def enabled_endpoint(self):
        if self.enabled and (not self.endpoint or not self.endpoint.startswith(('http://', 'https://'))):
            raise ValueError('QQ history endpoint must be an absolute HTTP URL')
        if self.enabled and not self.token_env:
            raise ValueError('QQ history token_env is required')
        return self


class QQConfig(SettingsModel):
    enabled: bool = False
    bot_id: str | None = None
    bot_names: tuple[str, ...] = ()
    max_body_bytes: int = Field(default=1048576, gt=0, le=16 * 1024 * 1024)
    history: QQHistoryConfig = Field(default_factory=QQHistoryConfig)

    @model_validator(mode='after')
    def enabled_account(self):
        if self.enabled and (not self.bot_id or not self.bot_id.isascii()
                             or not self.bot_id.isdecimal() or int(self.bot_id) <= 0):
            raise ValueError('QQ bot_id must be a positive identifier')
        return self


class JevConfig(SettingsModel):
    enabled: bool = False
    base_url: str = 'https://openrouter.ai/api'
    model: str = 'typesafe/jev-1.13'
    timeout_seconds: PositiveSeconds = 5.0
    attempts: int = Field(default=3, ge=1, le=10)


class MockConfig(SettingsModel):
    completion_delay_seconds: float = Field(default=0.05, ge=0, allow_inf_nan=False)


class ExecutionConfig(SettingsModel):
    mode: Literal['mock', 'gateway'] = 'mock'
    gateway_url: str = 'ws://127.0.0.1:8765/argus/rpc'

    @model_validator(mode='after')
    def service_url(self):
        if not self.gateway_url.startswith(('ws://', 'wss://')) or not self.gateway_url.rstrip('/').endswith('/argus/rpc'):
            raise ValueError('Gateway URL must point to the Argus service endpoint')
        return self


class ArgusConfig(SettingsModel):
    host: Literal['127.0.0.1'] = '127.0.0.1'
    port: int = Field(default=8766, ge=1, le=65535)
    log_level: Literal['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] = 'INFO'
    database_url: SecretStr | None = Field(default=None, exclude=True, repr=False)
    scan_interval_seconds: PositiveSeconds = 1.0
    quiet_seconds: PositiveSeconds = 5.0
    max_debounce_seconds: PositiveSeconds = 30.0
    max_items_per_wake: int = Field(default=20, ge=1, le=100)
    max_chars_per_wake: int = Field(default=20000, ge=1, le=100000)
    recent_context_items: int = Field(default=10, ge=0, le=100)
    recent_context_chars: int = Field(default=4000, ge=0, le=100000)
    max_concurrent_chats: int = Field(default=8, ge=1, le=100)
    submit_timeout_seconds: PositiveSeconds = 5.0
    max_submit_attempts: int = Field(default=3, ge=1, le=10)
    retry_delays_seconds: tuple[PositiveSeconds, ...] = (1.0, 2.0)
    outcome_timeout_seconds: PositiveSeconds = 60.0
    sensitive_keywords: tuple[str, ...] = ()
    default_permission_level: int = Field(default=2, ge=0, le=5)
    permission_overrides: dict[str, Annotated[int, Field(ge=0, le=5)]] = Field(default_factory=dict)
    jev: JevConfig = Field(default_factory=JevConfig)
    qq: QQConfig = Field(default_factory=QQConfig)
    mock: MockConfig = Field(default_factory=MockConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    service_host: str | None = None
    service_port: int | None = Field(default=None, ge=1, le=65535)

    @model_validator(mode='after')
    def retry_budget(self):
        if len(self.retry_delays_seconds) != self.max_submit_attempts - 1:
            raise ValueError('retry delays must match max_submit_attempts - 1')
        if (self.service_host is None) != (self.service_port is None):
            raise ValueError('Service host and port must be provided together')
        if self.service_host is not None:
            address = ipaddress.ip_address(self.service_host)
            if address.is_unspecified or not (address.is_loopback or address.is_private
                    or address in ipaddress.ip_network('100.64.0.0/10')):
                raise ValueError('Service listener requires a private LAN/VPN address')
        return self


def load_config(*, settings_path: Path | None = None) -> ArgusConfig:
    load_icarus_environment()
    path = settings_path or DEFAULT_SETTINGS_PATH
    values = json.loads(path.read_text(encoding='utf-8'))
    dsn = os.environ.get('ARGUS_DATABASE_URL')
    if not dsn:
        from sqlalchemy.engine import URL
        host = os.environ.get('ARGUS_POSTGRES_HOST')
        password = os.environ.get('ARGUS_POSTGRES_PASSWORD')
        if not host or not password:
            raise ValueError('ARGUS_DATABASE_URL or injected PostgreSQL topology and password is required')
        dsn = URL.create(
            'postgresql+asyncpg', username=os.environ.get('ARGUS_POSTGRES_USER', 'argus'),
            password=password, host=host, port=int(os.environ.get('ARGUS_POSTGRES_PORT', '5432')),
            database=os.environ.get('ARGUS_POSTGRES_DB', 'argus'),
        ).render_as_string(hide_password=False)
    values['database_url'] = SecretStr(dsn)
    config = ArgusConfig.model_validate(values)
    if config.qq.enabled and not os.getenv('ARGUS_QQ_REPORT_SECRET'):
        raise ValueError('ARGUS_QQ_REPORT_SECRET is required when QQ is enabled')
    if config.qq.enabled and config.jev.enabled and not os.getenv('ARGUS_JEV_API_KEY'):
        raise ValueError('ARGUS_JEV_API_KEY is required when Jev is enabled')
    if config.qq.enabled and config.qq.history.enabled and not os.getenv(config.qq.history.token_env):
        raise ValueError(f'{config.qq.history.token_env} is required when QQ history is enabled')
    return config
