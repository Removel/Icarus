"""Standalone configuration; no dependency on other Icarus applications."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Mapping

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, model_validator

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,128}$")]
SqlIdentifier = Annotated[str, StringConstraints(pattern=r"^[a-z_][a-z0-9_]{0,62}$")]


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class IdentityPerson(Config):
    person_id: Identifier
    display_name: Name


class Identity(Config):
    user: IdentityPerson
    agent: IdentityPerson = IdentityPerson(person_id="icarus", display_name="Icarus")

    @model_validator(mode="after")
    def distinct_ids(self):
        if self.user.person_id == self.agent.person_id:
            raise ValueError("user and agent IDs must differ")
        return self


class Server(Config):
    host: str = "0.0.0.0"
    port: int = Field(default=8767, ge=1, le=65535)


class GraphConfig(Config):
    host: Name = "127.0.0.1"
    port: int = Field(default=16379, ge=1, le=65535)
    graph: SqlIdentifier = "rela_mem"
    timeout_seconds: float = Field(default=5, gt=0, le=60)


class PostgresConfig(Config):
    host: Name = "127.0.0.1"
    port: int = Field(default=15432, ge=1, le=65535)
    database: SqlIdentifier = "rela_mem"
    user: SqlIdentifier = "rela_mem"
    schema_name: SqlIdentifier = Field(default="rela_mem", alias="schema")


class Settings(Config):
    identity: Identity
    server: Server = Server()
    falkordb: GraphConfig = GraphConfig()
    postgres: PostgresConfig = PostgresConfig()
    api_token: SecretStr = Field(exclude=True)
    graph_password: SecretStr = Field(exclude=True)
    pg_password: SecretStr = Field(exclude=True)

    @model_validator(mode="after")
    def nonempty_secrets(self):
        if not all(value.get_secret_value().strip() for value in (
            self.api_token, self.graph_password, self.pg_password
        )):
            raise ValueError("API token and database passwords must be nonempty")
        return self


def _apply_connection_overrides(data: dict, environ: Mapping[str, str]) -> None:
    for variable, section, field in (
        ("RELA_MEM_PG_HOST", "postgres", "host"),
        ("RELA_MEM_PG_PORT", "postgres", "port"),
        ("RELA_MEM_PG_DATABASE", "postgres", "database"),
        ("RELA_MEM_PG_USER", "postgres", "user"),
        ("RELA_MEM_PG_SCHEMA", "postgres", "schema"),
        ("RELA_MEM_FALKORDB_HOST", "falkordb", "host"),
        ("RELA_MEM_FALKORDB_PORT", "falkordb", "port"),
        ("RELA_MEM_FALKORDB_GRAPH", "falkordb", "graph"),
    ):
        if variable in environ:
            # Keep raw strings for Pydantic's coercion, range checks and sanitized errors.
            data.setdefault(section, {})[field] = environ[variable]


def load_settings(path: str | Path | None = None, environ: Mapping[str, str] | None = None) -> Settings:
    if environ is None:
        source = Path(__file__).resolve()
        repository = next((parent for parent in source.parents if (parent / "AGENTS.md").is_file() and (parent / "apps").is_dir()), None)
        if repository is not None and (repository / ".env").is_file():
            load_dotenv(repository / ".env", override=False)
        environ = os.environ
    path = Path(path or environ.get("RELA_MEM_CONFIG", "settings.json"))
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(
        api_token=environ.get("RELA_MEM_API_TOKEN", ""),
        graph_password=environ.get("RELA_MEM_FALKORDB_PASSWORD", ""),
        pg_password=environ.get("RELA_MEM_PG_PASSWORD", ""),
    )
    _apply_connection_overrides(data, environ)
    return Settings.model_validate(data)
