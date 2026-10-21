from redis.exceptions import RedisError, ResponseError
from falkordb.asyncio import FalkorDB

from errors import ServiceError


class GraphClient:
    def __init__(self, settings):
        config = settings.falkordb
        self.client = FalkorDB(host=config.host, port=config.port,
                              password=settings.graph_password.get_secret_value(),
                              socket_timeout=config.timeout_seconds,
                              socket_connect_timeout=config.timeout_seconds,
                              max_connections=20)
        self.graph = self.client.select_graph(config.graph)
        self.name = config.graph

    async def query(self, query: str, params: dict | None = None) -> list:
        try:
            result = await self.graph.query(query, params=params)
            return result.result_set
        except ResponseError as error:
            if "unique constraint violation" in str(error).lower():
                raise ServiceError("CONFLICT", "identity or relationship already exists", 409) from None
            raise ServiceError("UNAVAILABLE", "graph operation failed", 503) from error
        except (RedisError, OSError, TimeoutError) as error:
            raise ServiceError("UNAVAILABLE", "graph store unavailable", 503) from error

    async def command(self, *args):
        try:
            return await self.client.execute_command(*args)
        except (RedisError, OSError, TimeoutError) as error:
            raise ServiceError("UNAVAILABLE", "graph initialization failed", 503) from error

    async def ping(self):
        await self.query("RETURN 1")

    async def close(self):
        await self.client.connection.aclose()


def properties(entity) -> dict:
    return dict(entity.properties)
