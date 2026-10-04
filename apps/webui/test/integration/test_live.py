"""Opt-in checks against running backends; create only uniquely named test data."""

import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.parse import urlencode
from urllib.request import urlopen
from uuid import uuid4

import pytest
from playwright.sync_api import expect


pytestmark = pytest.mark.skipif(
    os.environ.get('WEBUI_LIVE_TESTS') != '1', reason='Set WEBUI_LIVE_TESTS=1 for real backends'
)
ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT.parents[1]


@pytest.fixture(scope='module')
def live_origin():
    if os.environ.get('WEBUI_LIVE_URL'):
        yield os.environ['WEBUI_LIVE_URL'], os.environ['WEBUI_LIVE_PASSWORD']
        return
    sys.path.insert(0, str(REPO / 'scripts' / 'icarus'))
    from environment import read_env_file

    env = {**read_env_file(REPO / '.env'), **os.environ}
    with socket.socket() as candidate:
        candidate.bind(('127.0.0.1', 0))
        port = candidate.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    password = uuid4().hex
    env.update(ICARUS_WEBUI_HOST='127.0.0.1', ICARUS_WEBUI_PORT=str(port),
               ICARUS_WEBUI_ORIGIN=origin, ICARUS_WEBUI_USER='live-test',
               ICARUS_WEBUI_PASSWORD=password)
    process = subprocess.Popen(['node', 'server/index.mjs'], cwd=ROOT, env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            assert process.poll() is None, 'Production entry exited'
            try:
                with urlopen(origin + '/health', timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(.05)
        else:
            pytest.fail('Production entry did not start')
        yield origin, password
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.fixture
def live(browser, live_origin):
    origin, password = live_origin
    context = browser.new_context(
        http_credentials={'username': 'live-test', 'password': password},
        extra_http_headers={'Origin': origin},
    )
    context.set_default_timeout(15000)
    try:
        yield context, origin
    finally:
        context.close()


def api(live, path, method='GET', data=None):
    context, origin = live
    response = context.request.fetch(origin + path, method=method, data=data, timeout=180000)
    assert response.ok, f'{method} {path.split("?")[0]}: HTTP {response.status}'
    return response.json()


def test_real_mem0_edit_history_pause_restore_and_delete(live):
    context, origin = live
    marker = 'webui-live-' + uuid4().hex
    created = api(live, '/api/mem0/memories', 'POST', {
        'user_id': marker, 'agent_id': 'webui-live-test', 'run_id': marker,
        'messages': [{'role': 'user', 'content': marker}], 'infer': False,
        'metadata': {'category': '验收', 'custom': 'preserve-live'},
    })
    memory_id = created['results'][0]['id']
    path = '/api/mem0/memories/' + memory_id
    try:
        page = context.new_page()
        page.goto(origin + '/#/memory/all')
        page.get_by_role('textbox', name='搜索记忆…').fill(marker)
        page.locator('.memory-entry-open').first.click()
        expect(page.locator('.memory-detail-text')).to_have_text(marker, timeout=60000)
        page.get_by_role('button', name='修正内容', exact=True).click()
        page.get_by_role('textbox', name='修正记忆内容').fill(marker + '-edited')
        page.get_by_role('button', name='保存修改', exact=True).click()
        expect(page.locator('.memory-detail-text')).to_have_text(marker + '-edited', timeout=60000)
        page.reload()
        expect(page.locator('.memory-detail-text')).to_have_text(marker + '-edited', timeout=60000)
        stored = api(live, path)
        assert stored['metadata']['custom'] == 'preserve-live'
        assert stored['memory'] == marker + '-edited'
        history = api(live, path + '/history')
        assert any(row.get('new_memory') == marker + '-edited' for row in history)
        page.get_by_role('button', name='暂停使用', exact=True).click()
        expect(page.get_by_role('button', name='恢复使用', exact=True)).to_be_visible(timeout=60000)
        assert api(live, path)['expiration_date']
        page.get_by_role('button', name='恢复使用', exact=True).click()
        expect(page.get_by_role('button', name='暂停使用', exact=True)).to_be_visible(timeout=60000)
        assert api(live, path).get('expiration_date') is None
        history = api(live, path + '/history')
        assert history[-2]['changes']['expiration_date'] == {'before': None, 'after': '1970-01-01'}
        assert history[-1]['changes']['expiration_date'] == {'before': '1970-01-01', 'after': None}
        page.locator('.memory-history > summary').click()
        expect(page.locator('.timeline-item').first).to_contain_text('恢复使用')
        expect(page.locator('.timeline-item').nth(1)).to_contain_text('暂停使用')
    finally:
        # Only the service ID returned for this invocation is removed.
        api(live, path, 'DELETE')
    assert api(live, path) is None


def test_real_openkb_isolated_create_read_and_delete(live):
    context, origin = live
    name = 'webui-live-' + uuid4().hex
    result = api(live, '/api/v1/init', 'POST', {'kb': name})
    assert result['created'] is True
    try:
        listing = api(live, '/api/v1/list', 'POST', {'kb': name})
        assert listing['documents'] == []
        assert api(live, '/api/v1/graph', 'POST', {'kb': name})['nodes'] == []
        existing = api(live, '/api/v1/page', 'POST', {'kb': name, 'path': 'index'})
        assert existing['content']
        page = context.new_page()
        page.goto(origin + '/#/knowledge/sources?' + urlencode({'base': name}))
        expect(page.get_by_role('combobox', name='选择知识库')).to_be_visible(timeout=30000)
        page.reload()
        expect(page.get_by_role('combobox', name='选择知识库')).to_be_visible(timeout=30000)
    finally:
        # The unique name was created above and must be confirmed by the backend.
        result = api(live, '/api/v1/kb/delete', 'POST', {'kb': name, 'confirm_name': name})
        assert result['deleted'] is True


@pytest.mark.skipif(os.environ.get('WEBUI_LIVE_KNOWLEDGE_MODEL') != '1', reason='Set WEBUI_LIVE_KNOWLEDGE_MODEL=1 for real knowledge compilation')
def test_real_openkb_import_compile_edit_and_reload(live):
    context, origin = live
    name = 'webui-live-' + uuid4().hex
    assert api(live, '/api/v1/init', 'POST', {'kb': name})['created'] is True
    try:
        page = context.new_page()
        page.goto(origin + '/#/knowledge/sources?' + urlencode({'base': name}))
        page.get_by_role('button', name='导入资料', exact=True).click()
        page.get_by_label('选择资料文件').set_input_files({
            'name': 'webui-check.md', 'mimeType': 'text/markdown',
            'buffer': b'# WebUI verification\n\nThe Aurora test project uses blue labels. Its launch date is October 1, 2026.\n',
        })
        with page.expect_response('**/api/v1/add', timeout=240000) as upload:
            page.get_by_role('button', name='开始导入', exact=True).click()
        response = upload.value
        assert response.ok, f'Knowledge import: HTTP {response.status}'
        result = response.json()
        assert result['added_count'] == 1 and result['failed_count'] == 0
        expect(page.locator('.operation-report')).to_contain_text('解析与知识生成完成')
        listing = api(live, '/api/v1/list', 'POST', {'kb': name})
        assert len(listing['documents']) == 1 and listing['summaries']
        source = api(live, '/api/v1/document/source', 'POST', {
            'kb': name, 'hash': listing['documents'][0]['hash'],
        })
        assert 'Aurora' in source['content']
        path = 'summaries/' + listing['summaries'][0]
        original = api(live, '/api/v1/page', 'POST', {'kb': name, 'path': path})['content']
        result = api(live, '/api/v1/page', 'PUT', {
            'kb': name, 'path': path, 'content': original + '\n\nWEBUI_PERSISTENCE_OK\n',
        })
        assert result['status'] == 'saved'
        assert 'WEBUI_PERSISTENCE_OK' in api(live, '/api/v1/page', 'POST', {'kb': name, 'path': path})['content']
        assert api(live, '/api/v1/graph', 'POST', {'kb': name})['nodes']
        page.goto(origin + '/#/knowledge/pages?' + urlencode({'base': name, 'page': path}))
        expect(page.get_by_role('dialog')).to_contain_text('WEBUI_PERSISTENCE_OK', timeout=30000)
        page.reload()
        expect(page.get_by_role('dialog')).to_contain_text('WEBUI_PERSISTENCE_OK', timeout=30000)
    finally:
        assert api(live, '/api/v1/kb/delete', 'POST', {'kb': name, 'confirm_name': name})['deleted'] is True


@pytest.mark.skipif(os.environ.get('WEBUI_LIVE_MODEL') != '1', reason='Set WEBUI_LIVE_MODEL=1 to invoke the configured model')
def test_real_gateway_model_history_reconnect_and_cancel(live, tmp_path):
    context, origin = live
    page = context.new_page()
    page.add_init_script('''
      window.liveSockets = [];
      const Original = window.WebSocket;
      window.WebSocket = class extends Original {
        constructor(...args) { super(...args); window.liveSockets.push(this); }
      };
    ''')
    page.goto(origin + '/#/chat?' + urlencode({'workspace': str(tmp_path.resolve())}))
    expect(page.get_by_text('已连接', exact=True)).to_be_visible(timeout=60000)
    page.get_by_role('button', name='新建会话').click()
    editor = page.get_by_role('textbox', name='发送消息')
    expect(editor).to_be_visible(timeout=60000)
    editor.fill('仅回复 ICARUS_LIVE_OK。不要调用工具，不记录记忆。')
    page.get_by_role('button', name='发送', exact=True).click()
    expect(page.locator('.chat-assistant')).to_contain_text('ICARUS_LIVE_OK', timeout=180000)
    expect(page.get_by_role('button', name='停止执行')).to_have_count(0, timeout=60000)
    page.reload()
    expect(page.locator('.chat-assistant')).to_contain_text('ICARUS_LIVE_OK', timeout=60000)
    page.evaluate('window.liveSockets.at(-1).close()')
    expect(page.get_by_role('alert')).to_contain_text('连接已断开')
    expect(page.get_by_text('已连接', exact=True)).to_be_visible(timeout=30000)
    expect(page.locator('.chat-assistant')).to_contain_text('ICARUS_LIVE_OK')
    editor.fill('请逐条列出一百种动物。不要调用工具，不记录记忆。')
    page.get_by_role('button', name='发送', exact=True).click()
    page.get_by_role('button', name='停止执行').click(timeout=30000)
    expect(page.get_by_role('button', name='停止执行')).to_have_count(0, timeout=60000)


@pytest.mark.skipif(os.environ.get('WEBUI_LIVE_MODEL') != '1', reason='Set WEBUI_LIVE_MODEL=1 to invoke tools through the model')
def test_real_gateway_memory_remember_and_bash(live, tmp_path):
    from urllib.parse import parse_qs, urlsplit

    context, origin = live
    page = context.new_page()
    page.goto(origin + '/#/chat?' + urlencode({'workspace': str(tmp_path.resolve())}))
    expect(page.get_by_text('已连接', exact=True)).to_be_visible(timeout=60000)
    page.get_by_role('button', name='新建会话').click()
    editor = page.get_by_role('textbox', name='发送消息')
    expect(editor).to_be_visible(timeout=60000)
    session = parse_qs(urlsplit(page.url).fragment.split('?', 1)[1])['session'][0]
    marker = 'ICARUS_TOOLS_' + uuid4().hex
    try:
        editor.fill(f'请实际调用 bash 执行 printf {marker}。再调用 memory_remember（is_workspace=true）记住本项目约定：所有验收报告使用标识 {marker}。这是需要长期遵守的项目约定。不要只口头回答，不调用其他写入工具。')
        page.get_by_role('button', name='发送', exact=True).click()
        expect(page.locator('.chat-tool > summary').filter(has_text='bash · 已完成')).to_be_visible(timeout=180000)
        expect(page.locator('.chat-tool > summary').filter(has_text='memory_remember · 已完成')).to_be_visible(timeout=180000)
        expect(page.get_by_role('button', name='停止执行')).to_have_count(0, timeout=180000)
        rows = api(live, '/api/mem0/memories?show_expired=true&top_k=1000')['results']
        created = [row for row in rows if row.get('metadata', {}).get('source_session_id') == session]
        assert created, 'Tool completed without persisting a memory for this session'
        assert any(marker in row['memory'] for row in created)
        page.reload()
        expect(page.locator('.chat-tool > summary').filter(has_text='memory_remember · 已完成')).to_be_visible(timeout=60000)
        expect(page.locator('.chat-tool > summary').filter(has_text='bash · 已完成')).to_be_visible(timeout=60000)
    finally:
        rows = api(live, '/api/mem0/memories?show_expired=true&top_k=1000')['results']
        for row in rows:
            if row.get('metadata', {}).get('source_session_id') == session:
                api(live, '/api/mem0/memories/' + row['id'], 'DELETE')


@pytest.mark.parametrize('scope', ['global', 'workspace'])
def test_real_manual_memory_is_recalled_after_browser_creation(live, tmp_path, scope):
    from hashlib import sha256
    context, origin = live
    page = context.new_page()
    page.goto(origin + '/#/memory/all')
    marker = 'WEBUI_RECALL_' + uuid4().hex
    text = f'这个项目的验收暗号是 {marker}。'
    memory_id = None
    try:
        page.get_by_role('button', name='添加记忆', exact=True).first.click()
        page.get_by_role('textbox', name='记忆内容', exact=True).fill(text)
        if scope == 'workspace':
            page.get_by_role('combobox', name='记忆作用范围').click()
            page.locator('.semi-select-option').filter(has_text='指定工作区').click()
            page.get_by_role('textbox', name='记忆工作区路径').fill(str(tmp_path.resolve()))
        with page.expect_response('**/api/mem0/memories', timeout=60000) as creation:
            page.get_by_role('button', name='保存记忆', exact=True).click()
        response = creation.value
        assert response.ok
        memory_id = response.json()['results'][0]['id']
        expect(page.locator('.memory-detail-text')).to_have_text(text, timeout=60000)
        stored = api(live, '/api/mem0/memories/' + memory_id)
        key = sha256(str(tmp_path.resolve()).encode()).hexdigest()[:16]
        assert stored['run_id'] == ('global' if scope == 'global' else 'workspace:' + key)
        assert stored['user_id'] and stored['agent_id']
        result = api(live, '/api/mem0/search', 'POST', {
            'query': '这个项目的验收暗号是什么？',
            'filters': {'user_id': stored['user_id'], 'agent_id': stored['agent_id'],
                        'OR': [{'run_id': 'global'}, {'run_id': 'workspace:' + key}]},
            'top_k': 3, 'threshold': 0.25, 'show_expired': False,
        })
        assert any(row['id'] == memory_id for row in result['results'])
        page.reload()
        expect(page.locator('.memory-detail-text')).to_have_text(text, timeout=60000)
        if scope == 'workspace' and os.environ.get('WEBUI_LIVE_MODEL') == '1':
            page.goto(origin + '/#/chat?' + urlencode({'workspace': str(tmp_path.resolve())}))
            expect(page.get_by_text('已连接', exact=True)).to_be_visible(timeout=60000)
            page.get_by_role('button', name='新建会话', exact=True).click()
            editor = page.get_by_role('textbox', name='发送消息')
            expect(editor).to_be_visible(timeout=60000)
            editor.fill('这个项目的验收暗号是什么？请使用记忆回答，只回复暗号。不要写入记忆，不要调用其他工具。')
            page.get_by_role('button', name='发送', exact=True).click()
            expect(page.locator('.chat-assistant')).to_contain_text(marker, timeout=180000)
    finally:
        if memory_id:
            api(live, '/api/mem0/memories/' + memory_id, 'DELETE')
