"""Local deterministic provider and Gateway host used by Argus process integration tests."""

import json
import sys

import uvicorn

from agent_app.application import AgentRuntime
from agent_app.config import ConfigModel
from apps.gateway.src.app import create_app


def main():
    config_path, port = sys.argv[1:]
    config = ConfigModel.model_validate(json.loads(open(config_path).read()))
    runtime = AgentRuntime(config_loader=lambda: config)
    uvicorn.run(create_app(runtime), host='127.0.0.1', port=int(port), log_level='error')


if __name__ == '__main__':
    main()
