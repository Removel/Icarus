"""Shared, browser-safe editing of the Agent's existing settings document."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

from pydantic import BaseModel, ConfigDict

from agent_app.config.config_loader import _settings_resource, get_config
from agent_app.config.config_model import (
    AgentSettings, ConfigModel, ModelRoleSettings, NamedLLMConfig, SkillSettings,
)


class EditableSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent: AgentSettings
    skill: SkillSettings
    model_settings: list[NamedLLMConfig]
    model_roles: ModelRoleSettings


class SettingsConflictError(ValueError):
    pass


class SettingsService:
    def __init__(self, path: Path | None = None):
        self._path = path

    def _file(self) -> Path:
        explicit = self._path or os.getenv("ICARUS_SETTINGS_PATH")
        resource = Path(explicit).expanduser() if explicit else _settings_resource()
        if not isinstance(resource, Path):
            raise ValueError("Set ICARUS_SETTINGS_PATH to an editable settings file")
        return resource

    def get(self) -> dict:
        path = self._file()
        raw = path.read_bytes()
        config = get_config(path)
        return {
            "revision": hashlib.sha256(raw).hexdigest(),
            "settings": EditableSettings.model_validate(config.model_dump(
                include=set(EditableSettings.model_fields)
            )).model_dump(mode="json"),
        }

    def update(self, revision: str, settings: EditableSettings) -> dict:
        path = self._file()
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != revision:
            raise SettingsConflictError("配置已被其他客户端修改，请重新载入后再保存。")
        changes = settings.model_dump(mode="json")
        # Validate role references and all existing cross-field constraints together.
        ConfigModel.model_validate({**get_config(path).model_dump(), **changes})
        data = json.loads(raw)
        data.update(changes)
        fd, temporary = tempfile.mkstemp(prefix=".icarus-settings-", suffix=".json", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return self.get()
