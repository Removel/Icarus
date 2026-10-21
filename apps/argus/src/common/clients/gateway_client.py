"""Gateway JSON-RPC transport retained for future real processing adapters."""

import asyncio
import json
from uuid import uuid4

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed


class GatewayTransportError(ConnectionError):
    pass


class GatewayBusinessError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class IcarusClient:
    def __init__(self, url, *, timeout_seconds=30, connector=connect):
        self.url, self.timeout_seconds, self._connector = url, timeout_seconds, connector
        self._connect_lock, self._send_lock = asyncio.Lock(), asyncio.Lock()
        self._socket, self._reader, self._pending = None, None, {}

    @property
    def connected(self):
        return self._socket is not None and self._reader is not None and not self._reader.done()

    async def connect(self):
        async with self._connect_lock:
            if self.connected:
                return
            if self._socket is not None:
                await self._socket.close()
            self._socket = await self._connector(self.url, max_size=16 * 1024 * 1024)
            self._reader = asyncio.create_task(self._read(), name='argus:gateway-reader')

    def _fail_pending(self, error):
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)

    async def _read(self):
        try:
            async for raw in self._socket:
                response = json.loads(raw)
                future = self._pending.get(str(response.get('id')))
                if future is None or future.done():
                    continue
                if 'error' in response:
                    error = response['error']
                    future.set_exception(GatewayBusinessError(
                        str((error.get('data') or {}).get('code', error.get('code'))),
                        str(error.get('message', 'Gateway error'))))
                else:
                    future.set_result(response.get('result'))
        except asyncio.CancelledError:
            raise
        except Exception:
            self._fail_pending(GatewayTransportError('Gateway response failed'))
        finally:
            self._fail_pending(GatewayTransportError('Gateway connection ended'))

    async def call(self, method, params):
        if not self.connected:
            try:
                await self.connect()
            except (OSError, TimeoutError, ConnectionError) as error:
                raise GatewayTransportError('Gateway connection failed') from error
        identifier, future = uuid4().hex, asyncio.get_running_loop().create_future()
        self._pending[identifier] = future
        try:
            async with self._send_lock:
                await self._socket.send(json.dumps({'jsonrpc': '2.0', 'id': identifier,
                                                    'method': method, 'params': params}))
            return await asyncio.wait_for(future, self.timeout_seconds)
        except GatewayBusinessError:
            raise
        except (OSError, TimeoutError, ConnectionError, ConnectionClosed) as error:
            raise GatewayTransportError('Gateway call failed') from error
        finally:
            self._pending.pop(identifier, None)
            if not future.done():
                future.cancel()

    async def close(self):
        if self._socket is not None:
            await self._socket.close()
            self._socket = None
        if self._reader is not None:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
            self._reader = None
