"""Explicit standalone service operations. No production data reset command."""
import argparse
import asyncio
from pathlib import Path
import sys

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "src"))

from config import load_settings


async def initialize(operation, settings):
    if operation == "init-graph":
        from repositories.graph_client import GraphClient
        from repositories.initialization import initialize_graph
        graph = GraphClient(settings)
        try:
            await initialize_graph(graph)
        finally:
            await graph.close()
    else:
        from repositories.database import Database
        database = Database(settings)
        try:
            await database.initialize()
        finally:
            await database.close()


def main():
    parser = argparse.ArgumentParser(description="rela-mem standalone management")
    parser.add_argument("operation", choices=["serve", "check-config", "init-graph", "init-postgres"])
    parser.add_argument("--config", default=None)
    args = parser.parse_args()
    settings = load_settings(args.config)
    if args.operation == "check-config":
        print("configuration valid")
    elif args.operation == "serve":
        import uvicorn
        from main import create_app
        uvicorn.run(create_app(settings), host=settings.server.host, port=settings.server.port)
    else:
        asyncio.run(initialize(args.operation, settings))
        print(args.operation + " complete")


if __name__ == "__main__":
    main()
