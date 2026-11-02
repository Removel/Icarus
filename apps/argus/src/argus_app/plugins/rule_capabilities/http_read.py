"""Read-only HTTP adapter helper; injected clients own credentials and lifecycle."""
import httpx
from argus_app.plugins.rule_capabilities.responses import ok, failure


async def read(client, request, method, path, **kwargs):
    if client is None:
        return failure(request, 'evidence_unavailable', unavailable=True)
    try:
        async with client.stream(method, path, timeout=5, **kwargs) as response:
            response.raise_for_status()
            chunks = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > 131000:
                    return failure(request, 'evidence_limit', 'evidence exceeds response limit')
                chunks.append(chunk)
            import json
            value = json.loads(b''.join(chunks))
            return ok(request, value if isinstance(value, dict) else {'items': value})
    except (httpx.HTTPError, ValueError) as exc:
        return failure(request, 'evidence_unavailable', str(exc), unavailable=True)
