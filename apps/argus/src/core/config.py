"""Validated application settings; secrets stay outside JSON configuration."""

import json
import os
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from packages.runtime_environment import load_icarus_environment

PositiveSeconds = Annotated[float, Field(gt=0, allow_inf_nan=False)]
DEFAULT_SETTINGS_PATH = Path(__file__).resolve().parents[2] / 'settings.json'


class SettingsModel(BaseModel):
    model_config = ConfigDict(extra='forbid', validate_default=True, hide_input_in_errors=True)


class QQConfig(SettingsModel):
    enabled: bool = False
    bot_id: str | None = None
    bot_names: tuple[str, ...] = ()
    max_body_bytes: int = Field(default=1048576, gt=0, le=16 * 1024 * 1024)

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

    @model_validator(mode='after')
    def retry_budget(self):
        if len(self.retry_delays_seconds) != self.max_submit_attempts - 1:
            raise ValueError('retry delays must match max_submit_attempts - 1')
        return self


def load_config(*, settings_path: Path | None = None) -> ArgusConfig:
    load_icarus_environment()
    path = settings_path or DEFAULT_SETTINGS_PATH
    values = json.loads(path.read_text(encoding='utf-8'))
    dsn = os.environ.get('ARGUS_DATABASE_URL')
    if not dsn:
        raise ValueError('ARGUS_DATABASE_URL is required')
    values['database_url'] = SecretStr(dsn)
    config = ArgusConfig.model_validate(values)
    if config.qq.enabled and not os.getenv('ARGUS_QQ_REPORT_SECRET'):
        raise ValueError('ARGUS_QQ_REPORT_SECRET is required when QQ is enabled')
    if config.qq.enabled and config.jev.enabled and not os.getenv('ARGUS_JEV_API_KEY'):
        raise ValueError('ARGUS_JEV_API_KEY is required when Jev is enabled')
    return config
