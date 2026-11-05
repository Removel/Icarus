"""Source regions are facts, not positions in a transport identifier."""
from types import SimpleNamespace

import pytest

from argus_app.plugins.protocols.qq_protocol.plugin import normalize_message
from argus_app.plugins.protocols.feishu_protocol.plugin import FeishuProtocol
from argus_app.plugins.workflow_rules.discovery import semantic_context, DEFAULT_RESPONSE_RULE


def test_qq_semantics_preserve_sender_time_and_safe_media():
    key, mid, content, detail = normalize_message(dict(post_type='message', self_id=100,
        user_id=200, group_id=42, message_id=1, message_type='group', time=1793707200,
        sender={'card':'Group name','nickname':'nickname'}, raw_message='[CQ:image,url=secret]',
        message=[{'type':'image','data':{'file':'image.png','url':'https://secret.invalid/signed'}}]))
    assert key == 'qq:100:group:42'
    assert detail.get('source', {}).get('kind') == 'group'
    assert detail['source']['region_id'] == '42'
    assert detail['source']['account_id'] == '100'
    assert content['sender_name'] == 'Group name'
    assert detail['sent_at'].endswith('+00:00')
    assert content['text'] == ''
    assert content['media'][0]['type'] == 'image'
    assert content['media'][0]['availability'] == 'unavailable'
    assert 'secret.invalid' not in str(content)


@pytest.mark.asyncio
async def test_feishu_content_type_is_not_region_kind():
    class Store:
        async def ingest(self, **kw): return SimpleNamespace(id='item', **kw)
    plugin = FeishuProtocol('feishu', Store())
    async def publish(event): pass
    plugin.bind_publisher(publish)
    row = await plugin.receive({'header':{'tenant_key':'tenant','event_type':'im.message.receive_v1'},
        'event':{'message':{'chat_id':'chat','chat_type':'group','message_id':'m',
        'message_type':'text','content':'{"text":"hello"}', 'create_time':'1793707200000',
        'root_id':'root','parent_id':'parent'}}})
    assert row.source_key == 'feishu:tenant:chat:chat'  # root alone does not repartition history
    view = semantic_context(row, [])['conversation']
    assert view['kind'] == 'group'
    assert view['content_type'] == 'text'
    assert row.detail['root_id'] == 'root'
    assert row.detail['sent_at'].endswith('+00:00')


def test_unknown_platform_never_guesses_qq_layout():
    row = SimpleNamespace(source_key='future:account:guess:peer', content={'message_type':'text'}, detail={})
    view = semantic_context(row, [])['conversation']
    assert view['platform'] == 'unknown'
    assert view['kind'] == 'unknown'
    assert view['peer_id'] == ''


@pytest.mark.parametrize('message', [None, [], ''])
def test_raw_cq_media_becomes_safe_semantics_not_a_signed_url(message):
    _, _, content, _ = normalize_message(dict(self_id=100, user_id=200,
        message_id=2, message_type='private', message=message,
        raw_message='看看[CQ:image,file=pic.png,url=https://secret.invalid/signed]'))
    assert content['text'] == '看看'
    assert content['media'][0]['type'] == 'image'
    assert 'secret.invalid' not in str(content)


def test_invalid_platform_time_is_not_invented():
    _, _, _, detail = normalize_message(dict(self_id=100, user_id=200, message_id=1,
        message_type='private', time='not-a-time', raw_message='hello'))
    assert 'sent_at' not in detail


@pytest.mark.parametrize('body', ['', '   '])
def test_pure_media_is_not_empty_message(body):
    # Execute the trusted default source with a deterministic semantic evidence port.
    calls = []
    def capability(name, arguments):
        calls.append(arguments)
        return {'match':True,'reason':'needs response'}
    namespace = {'capability':capability}
    exec(DEFAULT_RESPONSE_RULE, namespace)
    result = namespace['main']({'content':{'text':body,'media_summary':'[image: content unavailable]'}},
        {'goal':'discuss picture','progress':'','conversation':{}})
    assert result['passed'] is True
    assert calls[0]['text'] == '[image: content unavailable]'
