import json
import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[3]


def prepare_script(tmp_path):
    repo = tmp_path / 'repo'
    script = repo / 'apps/argus/scripts/test.sh'
    script.parent.mkdir(parents=True)
    shutil.copy2(ROOT / 'apps/argus/scripts/test.sh', script)
    python = repo / 'apps/argus/.venv/bin/python'
    python.parent.mkdir(parents=True)
    python.write_text('#!/usr/bin/env python3\nimport json,os,sys\n'
                      'with open(os.environ["CALL_LOG"],"a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")\n')
    python.chmod(0o755)
    return script


def test_script_forwards_pytest_arguments(tmp_path):
    script = prepare_script(tmp_path)
    log = tmp_path / 'calls.jsonl'
    env = {**os.environ, 'CALL_LOG': str(log),
           'ARGUS_TEST_DATABASE_URL': 'postgresql+asyncpg://test:test@127.0.0.1/argus_test'}
    result = subprocess.run(['bash', str(script), 'focused.py', '-x'], env=env, capture_output=True)
    assert result.returncode == 0, result.stderr.decode()
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[0] == ['-m', 'pytest', 'focused.py', '-x']


def test_script_cleans_owned_container_on_test_failure(tmp_path):
    script = prepare_script(tmp_path)
    python = script.parents[1] / '.venv/bin/python'
    python.write_text('#!/bin/sh\nexit 7\n')
    tools = tmp_path / 'tools'
    tools.mkdir()
    docker = tools / 'docker'
    docker.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$DOCKER_LOG"\n'
                      'case "$1" in run) printf "container-id\\n";; '
                      'port) printf "127.0.0.1:55499\\n";; esac\n')
    docker.chmod(0o755)
    log = tmp_path / 'docker.log'
    env = {**os.environ, 'PATH': f'{tools}:{os.environ["PATH"]}', 'DOCKER_LOG': str(log)}
    env.pop('ARGUS_TEST_DATABASE_URL', None)
    result = subprocess.run(['bash', str(script), 'focused.py'], env=env, capture_output=True)
    assert result.returncode == 7
    assert any(line.startswith('rm -f ') for line in log.read_text().splitlines())
