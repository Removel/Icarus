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
    heading = page.locator('.chat-conversation-heading h2')
    expect(heading).not_to_have_text('仅回复 ICARUS_LIVE_OK。不要调用工具，不记录记忆。', timeout=30000)
    generated_title = heading.inner_text()
    assert generated_title and len(generated_title) <= 80
    page.reload()
    expect(page.locator('.chat-assistant')).to_contain_text('ICARUS_LIVE_OK', timeout=60000)
    expect(heading).to_have_text(generated_title)
    page.evaluate('window.liveSockets.at(-1).close()')
    expect(page.get_by_role('alert')).to_contain_text('连接已断开')
    expect(page.get_by_text('已连接', exact=True)).to_be_visible(timeout=30000)
    expect(page.locator('.chat-assistant')).to_contain_text('ICARUS_LIVE_OK')
    editor.fill('请逐条列出一百种动物。不要调用工具，不记录记忆。')
    page.get_by_role('button', name='发送', exact=True).click()
    page.get_by_role('button', name='停止执行').click(timeout=30000)
    expect(page.get_by_role('button', name='停止执行')).to_have_count(0, timeout=60000)
    page.locator('.chat-session').first.hover()
    page.locator('.chat-session-delete').first.click()
    page.get_by_role('dialog', name='删除这段对话？', exact=True).get_by_role('button', name='删除', exact=True).click()
    expect(page.get_by_role('dialog', name='删除这段对话？', exact=True)).to_have_count(0)
    page.reload()
    expect(page.get_by_text('已连接', exact=True)).to_be_visible(timeout=30000)
    expect(page.locator('.chat-session')).to_have_count(0)


@pytest.mark.skipif(os.environ.get('WEBUI_LIVE_MODEL') != '1', reason='Set WEBUI_LIVE_MODEL=1 to invoke the configured model')
def test_real_gateway_queue_and_steer(live, tmp_path):
    context, origin = live
    page = context.new_page()
    page.add_init_script('''
        window.chatOperations = [];
        const Original = window.WebSocket;
        window.WebSocket = class extends Original {
            constructor(...args) {
                super(...args);
                this.addEventListener('message', event => {
                    const value = JSON.parse(event.data);
                    if (value.method === 'runtime.update' && value.params.type === 'task.finished')
                        window.chatOperations.push({kind: 'finished', task: value.params.task_id});
                });
            }
            send(raw) {
                const value = JSON.parse(raw);
                if (['session.submit', 'session.steer'].includes(value.method))
                    window.chatOperations.push({kind: value.method, params: value.params});
                super.send(raw);
            }
        };
    ''')
    page.goto(origin + '/#/chat?' + urlencode({'workspace': str(tmp_path.resolve())}))
    expect(page.get_by_text('已连接', exact=True)).to_be_visible(timeout=30000)
    page.get_by_role('button', name='新建会话').click()
    editor = page.get_by_role('textbox', name='发送消息')
    expect(editor).to_be_enabled(timeout=30000)
    try:
        editor.fill('仅回复 ICARUS_QUEUE_FIRST。不要调用工具，不记录记忆。')
        editor.press('Enter')
        editor.fill('仅回复 ICARUS_QUEUE_SECOND。不要调用工具，不记录记忆。')
        editor.press('Enter')
        expect(page.locator('.chat-queue li')).to_have_count(1, timeout=1000)
        editor.fill('补充：第一条仍只回复 ICARUS_QUEUE_FIRST，不调用工具，不记录记忆。')
        steer = page.get_by_role('button', name='引导当前任务', exact=True)
        expect(steer).to_be_enabled(timeout=60000)
        steer.click()
        expect(editor).to_have_value('', timeout=30000)
        expect(page.locator('.chat-message-state')).to_contain_text('引导', timeout=30000)
        expect(page.locator('.chat-assistant').last).to_contain_text('ICARUS_QUEUE_SECOND', timeout=180000)
        expect(page.get_by_role('button', name='停止执行')).to_have_count(0, timeout=60000)
        expect(page.locator('.chat-queue')).to_have_count(0)
        operations = page.evaluate('window.chatOperations')
        submissions = [item for item in operations if item['kind'] == 'session.submit']
        assert len(submissions) == 2
        assert submissions[0]['params']['submission_id'] != submissions[1]['params']['submission_id']
        steer_request = next(item for item in operations if item['kind'] == 'session.steer')
        first_finished = next(item for item in operations if item['kind'] == 'finished')
        assert steer_request['params']['task_id'] == first_finished['task']
        assert operations.index(first_finished) < operations.index(submissions[1])
        expect(page.locator('.chat-message-state')).to_have_text('引导已应用')
        expect(page.locator('.chat-user')).to_have_count(3)
        page.reload()
        expect(page.locator('.chat-user')).to_have_count(3, timeout=60000)
        expect(page.locator('.chat-assistant').last).to_contain_text('ICARUS_QUEUE_SECOND')
        expect(page.locator('.chat-message-state')).to_have_text('引导已应用')
    finally:
        while page.get_by_role('button', name='删除待发送消息 1', exact=True).count():
            page.get_by_role('button', name='删除待发送消息 1', exact=True).click()
        editor.fill('')
        stop = page.get_by_role('button', name='停止执行')
        if stop.count():
            stop.click()
            expect(stop).to_have_count(0, timeout=60000)
        page.locator('.chat-session').first.hover()
        page.locator('.chat-session-delete').first.click()
        dialog = page.get_by_role('dialog', name='删除这段对话？', exact=True)
        dialog.get_by_role('button', name='删除', exact=True).click()
        expect(dialog).to_have_count(0)


@pytest.mark.skipif(os.environ.get('WEBUI_LIVE_MODEL') != '1', reason='Set WEBUI_LIVE_MODEL=1 to invoke the configured model')
def test_real_gateway_immediate_message_history_and_early_stop(live, tmp_path):
    context, origin = live
    page = context.new_page()
    page.goto(origin + '/#/chat?' + urlencode({'workspace': str(tmp_path.resolve())}))
    expect(page.get_by_text('已连接', exact=True)).to_be_visible(timeout=60000)
    page.get_by_role('button', name='新建会话').click()
    editor = page.get_by_role('textbox', name='发送消息')
    expect(editor).to_be_enabled(timeout=60000)
    try:
        page.evaluate('''() => {
            window.replyFrames = [];
            new MutationObserver(() => {
                const length = document.querySelector('.chat-assistant .chat-markdown')?.textContent.length || 0;
                if (length > 0 && window.replyFrames.at(-1) !== length) window.replyFrames.push(length);
            }).observe(document.querySelector('.chat-transcript'), {childList: true, subtree: true, characterData: true});
        }''')
        editor.fill('仅回复 ICARUS_IMMEDIATE_OK。不要调用工具，不记录记忆。')
        editor.press('Enter')
        expect(page.locator('.chat-user')).to_have_count(1, timeout=1000)
        expect(page.get_by_text('正在思考中', exact=True)).to_be_visible(timeout=1000)
        page.screenshot(path='test-results/chat-thinking-placeholder.png', full_page=True)
        expect(editor).to_have_value('', timeout=1000)
        expect(editor).to_be_enabled(timeout=1000)
        assert page.get_by_text('发送中…', exact=True).count() == 0
        expect(page.locator('.chat-assistant')).to_contain_text('ICARUS_IMMEDIATE_OK', timeout=180000)
        expect(page.get_by_text('正在思考中', exact=True)).to_have_count(0)
        assert page.evaluate('window.replyFrames.length') > 1, 'Live output should have intermediate presentation frames'
        expect(page.get_by_role('button', name='停止执行')).to_have_count(0, timeout=60000)
        expect(page.locator('.chat-user')).to_have_count(1)
        page.reload()
        expect(page.locator('.chat-assistant')).to_contain_text('ICARUS_IMMEDIATE_OK', timeout=60000)
        expect(page.locator('.chat-user')).to_have_count(1)
        # A new session exercises stopping while the runtime is still loading.
        page.get_by_role('button', name='新建会话').click()
        expect(editor).to_be_enabled(timeout=60000)
        editor.fill('请逐条列出一百种动物。不要调用工具，不记录记忆。')
        editor.press('Enter')
        stop = page.get_by_role('button', name='停止执行')
        expect(stop).to_be_enabled(timeout=1000)
        stop.click()
        expect(stop).to_have_count(0, timeout=60000)
        expect(editor).to_be_enabled()
    finally:
        stop = page.get_by_role('button', name='停止执行')
        if stop.count():
            stop.click()
            expect(stop).to_have_count(0, timeout=60000)
        editor.fill('')
        while page.locator('.chat-session').count():
            page.locator('.chat-session').first.hover()
            page.locator('.chat-session-delete').first.click()
            dialog = page.get_by_role('dialog', name='删除这段对话？', exact=True)
            dialog.get_by_role('button', name='删除', exact=True).click()
            expect(dialog).to_have_count(0)


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


def test_real_memory_pages_filter_before_slicing(live):
    context, origin = live
    marker = 'WEBUI_PAGE_' + uuid4().hex
    ids = []
    try:
        for index in range(16):
            result = api(live, '/api/mem0/memories', 'POST', {
                'user_id': marker, 'agent_id': 'webui-live-test', 'run_id': 'global',
                'messages': [{'role': 'user', 'content': f'{marker} 内容 {index}'}],
                'infer': False, 'metadata': {'category': '分页验收'},
            })
            ids.append(result['results'][0]['id'])
        prefix = '/api/mem0/memories/page?' + urlencode({'user_id': marker, 'page_size': 12})
        first = api(live, prefix + '&page=1')
        second = api(live, prefix + '&page=2')
        assert first['total'] == second['total'] == 16
        assert len(first['results']) == 12 and len(second['results']) == 4
        assert {row['id'] for row in first['results'] + second['results']} == set(ids)
        narrowed = api(live, prefix + '&' + urlencode({'query': '内容 15', 'page': 2}))
        assert narrowed['total'] == 1 and narrowed['page'] == 1
        assert narrowed['results'][0]['id'] == ids[-1]
        assert context.request.get(origin + '/api/mem0/memories/page?page_size=0').status == 422
        page = context.new_page()
        page.goto(origin + '/#/memory/all')
        page.get_by_role('textbox', name='搜索记忆…').fill(marker)
        paging = page.get_by_role('navigation', name='记忆分页')
        expect(paging).to_contain_text('共 16 条 · 每页 15 条 · 第 1 / 2 页')
        expect(page.locator('.memory-entry')).to_have_count(15)
        paging.get_by_role('button', name='下一页').click()
        expect(paging).to_contain_text('第 2 / 2 页')
        expect(page.locator('.memory-entry')).to_have_count(1)
    finally:
        for memory_id in ids:
            api(live, '/api/mem0/memories/' + memory_id, 'DELETE')


@pytest.mark.skipif(os.environ.get('WEBUI_LIVE_MODEL') != '1', reason='Set WEBUI_LIVE_MODEL=1 to invoke memory tool')
def test_real_agent_memory_source_matches_browser(live, tmp_path):
    from urllib.parse import parse_qs, urlsplit
    from hashlib import sha256

    context, origin = live
    page = context.new_page()
    page.goto(origin + '/#/chat?' + urlencode({'workspace': str(tmp_path.resolve())}))
    expect(page.get_by_text('已连接', exact=True)).to_be_visible(timeout=60000)
    page.get_by_role('button', name='新建会话', exact=True).click()
    editor = page.get_by_role('textbox', name='发送消息')
    expect(editor).to_be_visible(timeout=60000)
    session = parse_qs(urlsplit(page.url).fragment.split('?', 1)[1])['session'][0]
    marker = 'WEBUI_SOURCE_' + uuid4().hex
    try:
        editor.fill(f'请实际调用 memory_remember（is_workspace=true）记录这个长期项目约定：所有验收报告使用标识 {marker}。不要调用其他工具。')
        page.get_by_role('button', name='发送', exact=True).click()
        expect(page.locator('.chat-tool > summary').filter(has_text='memory_remember · 已完成')).to_be_visible(timeout=180000)
        expect(page.get_by_role('button', name='停止执行')).to_have_count(0, timeout=180000)
        rows = api(live, '/api/mem0/memories?show_expired=true&top_k=1000')['results']
        created = [row for row in rows if row.get('metadata', {}).get('source_session_id') == session]
        assert created
        row = next(row for row in created if marker in row['memory'])
        key = sha256(str(tmp_path.resolve()).encode()).hexdigest()[:16]
        assert row['run_id'] == 'workspace:' + key
        assert row['metadata']['source_workspace_key'] == key
        assert row['metadata']['source_run_id']
        detail = api(live, '/api/mem0/memories/' + row['id'])
        assert row['metadata'] == detail['metadata']
        page.goto(origin + '/#/memory/all?' + urlencode({'entry': row['id']}))
        expect(page.locator('.memory-detail-text')).to_have_text(row['memory'])
        properties = page.locator('.memory-detail-properties')
        expect(properties).to_contain_text('Agent 记录')
        expect(properties).to_contain_text(session)
        expect(properties).to_contain_text(row['metadata']['source_run_id'])
        expect(properties).to_contain_text(key)
    finally:
        rows = api(live, '/api/mem0/memories?show_expired=true&top_k=1000')['results']
        for row in rows:
            if row.get('metadata', {}).get('source_session_id') == session:
                api(live, '/api/mem0/memories/' + row['id'], 'DELETE')


def test_real_gateway_lazy_create_and_delete(live, tmp_path):
    context, origin = live
    page = context.new_page()
    page.goto(origin + '/#/chat?' + urlencode({'workspace': str(tmp_path.resolve())}))
    expect(page.get_by_text('已连接', exact=True)).to_be_visible(timeout=30000)
    start = time.monotonic()
    page.get_by_role('button', name='新建会话').click()
    expect(page.get_by_role('textbox', name='发送消息')).to_be_enabled(timeout=30000)
    elapsed = time.monotonic() - start
    assert elapsed < 5, f'Lazy creation took {elapsed:.2f}s'
    row = page.locator('.chat-session-list [aria-current=page]')
    expect(row).to_be_visible()
    row.hover()
    page.get_by_role('button', name='删除会话：新会话', exact=True).click()
    page.get_by_role('dialog', name='删除这段对话？', exact=True).get_by_role('button', name='删除', exact=True).click()
    expect(page.get_by_role('dialog', name='删除这段对话？', exact=True)).to_have_count(0)
    expect(page.get_by_role('textbox', name='发送消息')).to_have_count(0)
    page.reload()
    expect(page.get_by_text('已连接', exact=True)).to_be_visible(timeout=30000)
    expect(page.locator('.chat-session')).to_have_count(0)


def test_real_graph_compact_view_retains_nodes_and_theme_colors(live):
    context, origin = live
    bases = api(live, '/api/v1/kbs')['knowledge_bases']
    assert bases, 'A knowledge base is required for the read-only graph check'
    page = context.new_page()
    page.set_viewport_size({'width': 1440, 'height': 1000})
    page.goto(origin + '/#/knowledge/graph?' + urlencode({'base': bases[0]['name']}))
    nodes = page.locator('.react-flow__node')
    expect(nodes.first).to_be_visible(timeout=30000)
    expect(page.get_by_role('button', name='全部连线', exact=True)).to_be_visible()
    node_count = nodes.count()
    edge_count = page.locator('.react-flow__edge').count()
    assert edge_count < node_count
    page.screenshot(path='test-results/graph-real-compact-light.png', full_page=True)
    positions = nodes.evaluate_all('els => els.map(el => el.style.transform)')
    page.get_by_role('button', name='全部连线', exact=True).click()
    page.wait_for_function('count => document.querySelectorAll(".react-flow__edge").length > count', arg=edge_count)
    expect(nodes).to_have_count(node_count)
    assert nodes.evaluate_all('els => els.map(el => el.style.transform)') == positions
    page.get_by_role('button', name='精简连线', exact=True).click()
    expect(page.locator('.react-flow__edge')).to_have_count(edge_count)
    page.emulate_media(color_scheme='dark')
    expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
    colors = page.locator('.canvas-legend i').evaluate_all('els => els.map(el => getComputedStyle(el).backgroundColor)')
    assert len(set(colors)) == 4
    page.screenshot(path='test-results/graph-real-compact-dark.png', full_page=True)
