from hashlib import sha1
import hmac
import json

from httpx import ASGITransport, AsyncClient

from apps.argus.src.core.config import ArgusConfig, QQConfig


async def test_oversize_stream_and_malformed_report_do_not_persist(database):
    from apps.argus.src.main import create_app
    app = create_app(ArgusConfig(qq=QQConfig(enabled=True, bot_id='123', max_body_bytes=32)),
                     database=database, qq_secret='secret')
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post('/api/argus/qq/onebot/events', content=b'x' * 33)
            assert response.status_code == 413
            raw = b'[]'
            response = await client.post('/api/argus/qq/onebot/events', content=raw, headers={
                'X-Self-ID': '123', 'X-Signature': 'sha1=' + hmac.new(b'secret', raw, sha1).hexdigest()})
            assert response.status_code == 422
            assert await app.state.repository.chats_to_scan() == ()
