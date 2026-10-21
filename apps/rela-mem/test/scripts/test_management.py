from pathlib import Path
import subprocess
import sys


APP = Path(__file__).resolve().parents[2]


def test_management_script_can_validate_config_without_database(tmp_path):
    import json
    import os

    config = tmp_path / "settings.json"
    config.write_text(json.dumps({"identity": {"user": {"person_id": "me", "display_name": "我"}}}), encoding="utf-8")
    result = subprocess.run([sys.executable, str(APP / "scripts/manage.py"), "check-config"],
        env=os.environ | {"RELA_MEM_CONFIG": str(config), "RELA_MEM_API_TOKEN": "not-public",
                         "RELA_MEM_FALKORDB_PASSWORD": "graph-secret", "RELA_MEM_PG_PASSWORD": "pg-secret"},
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "configuration valid" in result.stdout
    assert "graph-secret" not in result.stdout + result.stderr


def test_management_help_has_explicit_init_and_smoke_operations():
    result = subprocess.run([sys.executable, str(APP / "scripts/manage.py"), "--help"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "init-graph" in result.stdout and "init-postgres" in result.stdout


def test_compose_configuration_requires_secrets_and_has_isolated_ports(tmp_path):
    import os

    result = subprocess.run(["docker", "compose", "-f", str(APP / "docker-compose.yaml"), "config", "--format", "json"],
        env=os.environ | {"ICARUS_DATA_DIR": str(tmp_path), "RELA_MEM_API_TOKEN": "test-token",
                         "RELA_MEM_FALKORDB_PASSWORD": "graph-pw", "RELA_MEM_PG_PASSWORD": "pg-pw",
                         "RELA_MEM_PG_HOST": "postgres", "RELA_MEM_PG_PORT": "5432", "RELA_MEM_PG_DATABASE": "rela_mem",
                         "RELA_MEM_PG_USER": "rela_mem", "RELA_MEM_PG_SCHEMA": "rela_mem", "RELA_MEM_FALKORDB_HOST": "falkordb",
                         "RELA_MEM_FALKORDB_PORT": "6379", "RELA_MEM_FALKORDB_GRAPH": "rela_mem"},
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    import json
    services = json.loads(result.stdout)["services"]
    assert set(services) == {"rela-mem"}
    assert services["rela-mem"]["ports"][0]["published"] == "8767"
    assert services["rela-mem"]["environment"]["RELA_MEM_PG_HOST"] == "postgres"
    assert services["rela-mem"]["environment"]["RELA_MEM_FALKORDB_HOST"] == "falkordb"
    assert "depends_on" not in services["rela-mem"]
    assert all(v.get("type") == "bind" for s in services.values() for v in s.get("volumes", []))
