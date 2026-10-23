"""Search/Fetch HTTP boundary. Every request shares a conservative start gate."""

import asyncio
import weakref
from urllib.parse import urlsplit
import httpx


_gates: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, tuple[asyncio.Lock, list[float]]] = weakref.WeakKeyDictionary()


class SearchUnavailable(RuntimeError):
    def __init__(self, public_message):
        self.public_message = public_message
        super().__init__(public_message)


async def wait_request_slot(started=None):
    loop = asyncio.get_running_loop()
    lock, clock = _gates.setdefault(loop, (asyncio.Lock(), [0.0]))
    async with lock:
        await asyncio.sleep(max(0, clock[0] - loop.time()))
        if started:
            await started()
        clock[0] = loop.time() + 0.21


class SearchHttpBackend:
    def __init__(self, endpoint, api_key, *, transport=None):
        endpoint = endpoint.rstrip("/")
        url = urlsplit(endpoint)
        if url.scheme not in {"https", "http"} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("Search endpoint must be an absolute HTTP(S) URL without credentials, query or fragment")
        if url.scheme != "https" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Search endpoint must use HTTPS outside loopback")
        self.endpoint = endpoint if url.path.rstrip("/") else endpoint + "/v1"
        self.api_key = api_key
        self.client = httpx.AsyncClient(transport=transport, follow_redirects=False)

    async def _post(self, name, payload, started=None):
        await wait_request_slot(started)
        try:
            response = await self.client.post(self.endpoint + "/tools/" + name, json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"}, timeout=15 if name == "search" else 20)
            if response.status_code in {404, 405, 501} and name == "search":
                raise SearchUnavailable("该中转不支持深度检索，已继续正常回答。")
            response.raise_for_status()
            return response.json()
        except SearchUnavailable:
            raise
        except Exception:
            # Do not put response bodies, URLs or credentials into public errors.
            raise SearchUnavailable("联网搜索暂不可用，已继续正常回答。") from None

    async def search(self, query, question, started=None):
        result = await self._post("search", {"query": query[:128], "count": 10,
            "ori_question": question[:128], "need_content": True}, started)
        return result.get("results") if isinstance(result, dict) else None

    async def fetch(self, url):
        result = await self._post("fetch", {"url": url})
        if not isinstance(result, dict):
            raise ValueError("Fetch response is not an object")
        raw_data = result.get("data")
        data = raw_data if isinstance(raw_data, dict) else {}
        title = result.get("title") or data.get("title") or ""
        body = result.get("content") or result.get("markdown") or result.get("text") or data.get("content") or ""
        if not isinstance(body, str):
            raise ValueError("Fetch content must be text")
        return str(title)[:500], body

    async def aclose(self):
        await self.client.aclose()
