"""Argus process entry; the repository lifecycle invokes this installed module."""
import argparse
import logging

import uvicorn

from argus_app.application import create_app
from argus_app.config import ArgusConfig


def main():
    parser = argparse.ArgumentParser(description="Argus plugin observation runtime")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    config = ArgusConfig.from_environment()
    changes = {key: value for key, value in vars(args).items() if value is not None}
    config = ArgusConfig.model_validate({**config.model_dump(), **changes})
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(config), host=config.host, port=config.port)


if __name__ == "__main__":
    main()
