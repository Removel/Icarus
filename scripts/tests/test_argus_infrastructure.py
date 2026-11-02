"""Argus is a process client of shared PostgreSQL, never its owner."""
from pathlib import Path
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "icarus"))


def test_argus_uses_shared_postgres_with_independent_role():
    from registry import load_registry
    registry = load_registry(ROOT)
    assert "argus" in registry.names(), "Argus must be registered with the existing lifecycle"
    app = registry.get("argus")
    assert app.provisioning == "process"
    postgres = app.requires["postgres"]
    assert postgres.connection.database == "argus"
    assert postgres.connection.user == "argus"
    assert postgres.connection.password_env == "ARGUS_PG_PASSWORD"
    config = tomllib.loads((ROOT / "icarus.toml").read_text())
    assert config["components"]["argus"]["default_stack"] is False
    assert not (ROOT / "apps/argus/compose.yaml").exists()


def test_argus_provisioning_is_optional_and_non_destructive():
    script = (ROOT / "infra/postgres/init/db-init.sh").read_text()
    assert "ARGUS_PG_PASSWORD" in script
    assert 'create_database argus argus "$ARGUS_PG_PASSWORD"' in script
    assert "DROP DATABASE" not in script
    compose = (ROOT / "infra/postgres/compose.yaml").read_text()
    assert "ARGUS_PG_PASSWORD" in compose
