"""App-owned configuration; credentials never live in topology or rule programs."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class ArgusConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    database_url: SecretStr
    data_dir: Path
    host: str = "127.0.0.1"
    port: int = Field(default=8770, ge=1, le=65535)
    agent_token: SecretStr
    gateway_token: SecretStr
    gateway_url: str = "http://127.0.0.1:8765"
    public_url: str = "http://127.0.0.1:8770"
    qq_token: SecretStr = SecretStr("")
    feishu_verification_token: SecretStr = SecretStr("")
    feishu_encrypt_key: SecretStr = SecretStr("")
    scan_interval: float = Field(default=1.0, gt=0, le=60)
    rule_timeout: float = Field(default=10.0, gt=0, le=30)
    max_body_bytes: int = Field(default=262144, ge=1024, le=1048576)
    auto_scan: bool = True
    discovery_enabled: bool = True
    discovery_rule_path: str | None = None
    semantic_api_key: SecretStr = SecretStr("")
    semantic_base_url: str | None = None
    semantic_model: str | None = None
    safety_review_enabled: bool = False
    safety_review_budget: int = Field(default=16, ge=1, le=1000)
    memory_endpoint: str | None = None
    memory_token: SecretStr = SecretStr("")
    memory_user_id: str = ""
    memory_agent_id: str = ""
    memory_workspace_key: str = ""
    relationship_endpoint: str | None = None
    relationship_token: SecretStr = SecretStr("")
    knowledge_endpoint: str | None = None
    knowledge_token: SecretStr = SecretStr("")
    knowledge_base: str = ""

    @classmethod
    def from_environment(cls) -> "ArgusConfig":
        url = os.getenv("ARGUS_DATABASE_URL")
        if not url:
            password = os.getenv("ARGUS_PG_PASSWORD")
            if not password:
                raise ValueError("ARGUS_PG_PASSWORD or ARGUS_DATABASE_URL is required")
            user = quote(os.getenv("ARGUS_PG_USER", "argus"), safe="")
            password = quote(password, safe="")
            host = os.getenv("ARGUS_PG_HOST", "127.0.0.1")
            port = int(os.getenv("ARGUS_PG_PORT", "15432"))
            database = quote(os.getenv("ARGUS_PG_DATABASE", "argus"), safe="")
            url = f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{database}"
        if not url.startswith("postgresql+asyncpg://"):
            raise ValueError("Argus requires PostgreSQL+asyncpg")
        token = os.getenv("ARGUS_AGENT_API_TOKEN", "")
        gateway_token = os.getenv("ARGUS_GATEWAY_TOKEN", "")
        if not token or not gateway_token:
            raise ValueError("ARGUS_AGENT_API_TOKEN and ARGUS_GATEWAY_TOKEN are required")
        data_root = os.getenv("ARGUS_DATA_DIR")
        if not data_root:
            shared = os.getenv("ICARUS_DATA_DIR")
            if not shared:
                raise ValueError("ARGUS_DATA_DIR or ICARUS_DATA_DIR is required")
            data_root = str(Path(shared) / "services" / "argus")
        return cls(database_url=SecretStr(url), data_dir=Path(data_root).expanduser().resolve(),
                   agent_token=SecretStr(token), gateway_token=SecretStr(gateway_token),
                   host=os.getenv("ARGUS_HOST", "127.0.0.1"), port=int(os.getenv("ARGUS_PORT", "8770")),
                   gateway_url=os.getenv("ARGUS_GATEWAY_ENDPOINT", "http://127.0.0.1:8765").replace("ws://", "http://").replace("wss://", "https://").removesuffix("/rpc"),
                   public_url=os.getenv("ARGUS_PUBLIC_URL", "http://127.0.0.1:8770"),
                   qq_token=SecretStr(os.getenv("ARGUS_QQ_REPORT_SECRET", "")),
                   feishu_verification_token=SecretStr(os.getenv("ARGUS_FEISHU_VERIFICATION_TOKEN", "")),
                   feishu_encrypt_key=SecretStr(os.getenv("ARGUS_FEISHU_ENCRYPT_KEY", "")),
                   semantic_api_key=SecretStr(os.getenv("ARGUS_JEV_API_KEY", "")),
                   discovery_enabled=os.getenv("ARGUS_DISCOVERY_ENABLED", "true").lower() in {"true", "1", "yes"},
                   discovery_rule_path=os.getenv("ARGUS_DISCOVERY_RULE_PATH"),
                   semantic_base_url=os.getenv("ARGUS_JEV_BASE_URL"), semantic_model=os.getenv("ARGUS_JEV_MODEL"),
                   safety_review_enabled=os.getenv('ARGUS_SAFETY_REVIEW_ENABLED', 'false').lower() in {'true', '1', 'yes'},
                   safety_review_budget=int(os.getenv('ARGUS_SAFETY_REVIEW_BUDGET', '16')),
                   memory_endpoint=os.getenv("ICARUS_MEM0_ENDPOINT"),
                   memory_token=SecretStr(os.getenv("ICARUS_MEM0_API_KEY", "")),
                   memory_user_id=os.getenv("ARGUS_MEMORY_USER_ID", ""),
                   memory_agent_id=os.getenv("ARGUS_MEMORY_AGENT_ID", ""),
                   memory_workspace_key=os.getenv("ARGUS_MEMORY_WORKSPACE_KEY", ""),
                   relationship_endpoint=os.getenv("ICARUS_RELATIONSHIP_ENDPOINT"),
                   relationship_token=SecretStr(os.getenv("RELA_MEM_API_TOKEN", "")),
                   knowledge_endpoint=os.getenv("ICARUS_OPENKB_ENDPOINT"),
                   knowledge_token=SecretStr(os.getenv("ICARUS_OPENKB_API_TOKEN", "")),
                   knowledge_base=os.getenv("ARGUS_KNOWLEDGE_BASE", ""))
