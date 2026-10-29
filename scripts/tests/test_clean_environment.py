"""The checked-in topology provides shared databases without machine overrides."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "icarus"))
from compose_runtime import ComposeRuntime
from configuration import load_configuration
from graph import required_closure
from injection import build_child_environment

ROOT = Path(__file__).resolve().parents[2]


def clean_registry(tmp_path, overrides=None):
    shutil.copy2(ROOT / "icarus.toml", tmp_path / "icarus.toml")
    return load_configuration(tmp_path, overrides=overrides)


def test_clean_topology_starts_shared_postgres_before_mem0(tmp_path):
    registry = clean_registry(tmp_path)
    order = required_closure(registry, ["mem0", "rela-mem"])
    assert order.count("postgres") == 1
    assert order.index("postgres") < order.index("mem0")
    assert order.index("postgres") < order.index("rela-mem")
    assert registry.data_dir == "~/.icarus"
    assert registry.get("postgres").data_dir == "services/postgres"
    assert registry.get("falkordb").data_dir == "services/falkordb"
    assert "argus" not in registry.names()


@pytest.mark.parametrize("app,database,user", [
    ("mem0", "mem0_app", "mem0_local"), ("rela-mem", "rela_mem", "rela_mem"),
])
def test_application_connections_use_shared_network_with_distinct_accounts(tmp_path, app, database, user):
    registry = clean_registry(tmp_path)
    component = registry.get(app)
    edge = component.requires["postgres"].connection
    environment = build_child_environment(component, registry, {
        name: "fixture-secret" for name in component.secret_env.values()
    }).environment
    assert environment[edge.host_env] == "postgres"
    assert environment[edge.port_env] == "5432"
    assert environment[edge.database_env] == database
    assert environment[edge.user_env] == user
    assert registry.get("postgres").env["ICARUS_POSTGRES_USER"] == "icarus_admin"
    if app == "mem0":
        assert environment["ICARUS_MEM0_APP_DB_NAME"] == "mem0_app"


def test_remote_postgres_does_not_use_container_dns(tmp_path):
    registry = clean_registry(tmp_path, {"postgres": {
        "mode": "remote", "host": "database.internal", "port": 25432,
    }})
    mem0 = registry.get("mem0")
    environment = build_child_environment(mem0, registry, {
        name: "fixture-secret" for name in mem0.secret_env.values()
    }).environment
    assert environment["ICARUS_MEM0_POSTGRES_HOST"] == "database.internal"
    assert environment["ICARUS_MEM0_DB_PORT"] == "25432"


def test_compose_network_follows_declared_topology(tmp_path):
    shutil.copytree(ROOT / "infra", tmp_path / "infra")
    registry = clean_registry(tmp_path, {"postgres": {"network": "isolated-test"}})
    runtime = ComposeRuntime(tmp_path, tmp_path / "data", {}, registry)
    assert runtime._environment(registry.get("postgres"))["ICARUS_NETWORK"] == "isolated-test"


@pytest.mark.parametrize("app,path", [
    ("mem0", "apps/mem0/server/docker-compose.yaml"),
    ("openkb", "apps/openkb/docker-compose.yaml"),
    ("rela-mem", "apps/rela-mem/docker-compose.yaml"),
])
def test_app_compose_keeps_single_service_and_explicit_development_image(app, path):
    document = yaml.safe_load((ROOT / path).read_text())
    assert set(document["services"]) == {app}
    service = document["services"][app]
    variable = app.upper().replace("-", "_")
    assert service["image"] == "${ICARUS_" + variable + "_IMAGE:-icarus-" + app + ":dev}"
    assert service["pull_policy"] == "never"
    assert not service["build"].get("tags")


def run_initializer(tmp_path, *, fail_database=None):
    tools = tmp_path / "tools"
    tools.mkdir()
    psql = tools / "psql"
    psql.write_text(
        "#!/usr/bin/env python3\nimport json, os, sys\nfrom pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "with Path(os.environ['SQL_LOG']).open('a') as log:\n"
        " log.write(json.dumps({'args': args, 'sql': sys.stdin.read()}) + '\\n')\n"
        "if os.environ.get('FAIL_DATABASE') and args[args.index('--dbname') + 1] == os.environ['FAIL_DATABASE']:\n"
        " sys.exit(1)\n"
    )
    psql.chmod(0o755)
    log = tmp_path / "sql.jsonl"
    environment = {
        "PATH": str(tools) + os.pathsep + os.environ["PATH"], "SQL_LOG": str(log),
        "POSTGRES_USER": "icarus_admin", "ICARUS_MEM0_POSTGRES_PASSWORD": "p@ss:'/?#word",
        "RELA_MEM_PG_PASSWORD": "rela-secret",
    }
    if fail_database:
        environment["FAIL_DATABASE"] = fail_database
    result = subprocess.run(["bash", str(ROOT / "infra/postgres/init/db-init.sh")],
                            env=environment, input="", capture_output=True, text=True)
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return result, calls


def test_empty_cluster_initialization_provisions_only_current_applications(tmp_path):
    result, calls = run_initializer(tmp_path)
    assert result.returncode == 0, result.stderr
    creations = [call for call in calls if any(arg.startswith("--set=role=") for arg in call["args"])]
    assert len(creations) == 2
    assert "--set=role=mem0_local" in creations[0]["args"]
    assert "--set=database=mem0_app" in creations[0]["args"]
    assert "--set=password=p@ss:'/?#word" in creations[0]["args"]
    assert "--set=role=rela_mem" in creations[1]["args"]
    for call in creations:
        assert "NOSUPERUSER NOCREATEDB NOCREATEROLE" in call["sql"]
        assert "PASSWORD %L" in call["sql"]
    for database in ("mem0_app", "rela_mem"):
        assert any(call["args"][call["args"].index("--dbname") + 1] == database
                   and "CREATE EXTENSION IF NOT EXISTS vector" in call["args"] for call in calls)
    assert any("CREATE SCHEMA IF NOT EXISTS rela_mem AUTHORIZATION rela_mem" in call["args"]
               for call in calls)
    assert "argus" not in json.dumps(calls)
    assert "p@ss" not in result.stdout + result.stderr


def test_empty_cluster_initialization_stops_on_database_failure(tmp_path):
    result, calls = run_initializer(tmp_path, fail_database="mem0_app")
    assert result.returncode != 0
    assert len(calls) == 3
    assert calls[-1]["args"][calls[-1]["args"].index("--dbname") + 1] == "mem0_app"
    assert not any("CREATE SCHEMA IF NOT EXISTS rela_mem AUTHORIZATION rela_mem" in call["args"]
                   for call in calls)
