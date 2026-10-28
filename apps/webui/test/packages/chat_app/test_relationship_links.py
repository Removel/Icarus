import json
from playwright.sync_api import expect
from test.packages.chat_app.test_chat import gateway
from test.rela_service import fake_rela


def test_relationship_result_links_use_id_and_keep_chat_session(page):
    rela = fake_rela(page)
    state, emit = gateway(page)
    first = rela['people'][0]
    emit('tool.completed', {'call_id': 'rela-find', 'tool_name': 'relationship_find', 'success': True, 'output_preview': json.dumps({'items': [first], 'ambiguous': False, 'truncated': False})})
    page.locator('.chat-process > summary').click()
    page.locator('.chat-tool').get_by_text('relationship_find', exact=True).click()
    page.get_by_role('link', name='查看人物档案：王明，p_000001_alpha', exact=True).click()
    dialog = page.get_by_role('dialog', name='人物档案', exact=True)
    expect(dialog.get_by_text('p_000001_alpha', exact=True)).to_be_visible()
    dialog.get_by_role('button', name='关闭人物档案', exact=True).click()
    page.get_by_role('link', name='返回对话', exact=True).click()
    expect(page.locator('.chat-tool').get_by_text('relationship_find', exact=True)).to_be_visible()
    assert not any(call['method'] in ('session.create', 'session.submit') for call in state['calls'])


def test_truncated_or_unrelated_tool_output_is_not_guessed(page):
    _, emit = gateway(page)
    emit('tool.completed', {'call_id': 'bad-json', 'tool_name': 'relationship_find', 'success': True, 'output_preview': '{"items": [{"person_id": "p_partial"'})
    emit('tool.completed', {'call_id': 'other', 'tool_name': 'other_tool', 'success': True, 'output_preview': json.dumps({'person_id': 'p_not_a_relationship_result'})})
    expect(page.locator('.chat-tool')).to_have_count(2)
    expect(page.locator('.chat-tool-person-link')).to_have_count(0)
