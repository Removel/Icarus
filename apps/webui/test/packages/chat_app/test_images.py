from test.navigation import follow_link
import base64
import re

import pytest

from playwright.sync_api import expect
from test.packages.chat_app.test_chat import gateway
from test.navigation import open_sessions


PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
RESOURCE = {'resource_id': 'webui/' + 'a' * 32 + '.png', 'media_type': 'image/png'}
ASSET = {'resource_id': 'assets/' + 'b' * 64 + '.png', 'media_type': 'image/png'}


def uploads(page, *, fail_once=False):
    calls = []
    def handle(route):
        if route.request.method == 'POST':
            calls.append(route.request.post_data_buffer)
            if fail_once and len(calls) == 1:
                route.fulfill(status=503, body='unavailable')
            else:
                route.fulfill(json=RESOURCE)
        else:
            route.fulfill(content_type='image/png', body=PNG)
    page.route('**/resources*', handle)
    return calls


def pick(page):
    page.locator('input[type=file]').set_input_files({'name': 'sample.png', 'mimeType': 'image/png', 'buffer': PNG})
    expect(page.locator('.chat-image-input').get_by_role('status')).to_have_text('已上传')


def submitted(state, method='session.submit'):
    return [call['params'] for call in state['calls'] if call['method'] == method]


def test_image_upload_text_send_event_and_history(page):
    state, emit = gateway(page)
    calls = uploads(page)
    pick(page)
    page.get_by_role('textbox', name='发送消息').fill('看这张图片')
    page.get_by_role('button', name='发送', exact=True).click()
    expect(page.locator('.chat-user')).to_contain_text('看这张图片')
    expect(page.locator('.chat-user img')).to_be_visible()
    assert calls == [PNG]
    assert submitted(state)[0]['resources'] == [RESOURCE]
    emit('user.message', {'text': '看这张图片', 'resources': [ASSET], 'submission_id': submitted(state)[0]['submission_id']})
    expect(page.locator('.chat-user')).to_have_count(1)
    expect(page.locator('.chat-user img')).to_have_attribute('src', re.compile('.*resource_id=assets.*session_id=session-1'))
    page.reload()
    expect(page.locator('.chat-user img')).to_be_visible()
    assert page.locator('.chat-user img').evaluate('img => img.complete && img.naturalWidth > 0')


def test_pure_image_first_message_creates_session_and_sends(page):
    state, _ = gateway(page, selected='')
    uploads(page)
    pick(page)
    page.get_by_role('button', name='发送', exact=True).click()
    expect(page.locator('.chat-user img')).to_be_visible()
    assert submitted(state)[0]['prompt'] == ''
    assert submitted(state)[0]['resources'] == [RESOURCE]
    assert len([call for call in state['calls'] if call['method'] == 'session.create']) == 1


def test_upload_failure_retry_and_remove_preserve_text(page):
    state, _ = gateway(page)
    calls = uploads(page, fail_once=True)
    editor = page.get_by_role('textbox', name='发送消息')
    editor.fill('保留文字')
    page.locator('input[type=file]').set_input_files({'name': 'sample.png', 'mimeType': 'image/png', 'buffer': PNG})
    expect(page.get_by_role('button', name='重试上传')).to_be_visible()
    expect(page.get_by_role('button', name='发送', exact=True)).to_be_disabled()
    assert not submitted(state)
    expect(editor).to_have_value('保留文字')
    page.get_by_role('button', name='重试上传').click()
    expect(page.locator('.chat-image-input').get_by_role('status')).to_have_text('已上传')
    assert len(calls) == 2
    page.get_by_role('button', name='移除图片 1').click()
    expect(page.locator('.chat-image-input')).to_have_count(0)
    expect(editor).to_have_value('保留文字')


def test_paste_drop_validation_and_queue_edit(page):
    state, emit = gateway(page)
    uploads(page)
    emit('task.started', {})
    editor = page.get_by_role('textbox', name='发送消息')
    editor.evaluate('''(node, bytes) => {
        const data = new DataTransfer();
        data.items.add(new File([new Uint8Array(bytes)], 'paste.png', {type: 'image/png'}));
        node.dispatchEvent(new ClipboardEvent('paste', {clipboardData: data, bubbles: true, cancelable: true}));
    }''', list(PNG))
    expect(page.locator('.chat-image-input').get_by_role('status')).to_have_text('已上传')
    page.get_by_role('button', name='排队发送', exact=True).click()
    expect(page.locator('.chat-queue')).to_contain_text('1 张图片')
    assert not submitted(state)
    page.get_by_role('button', name='编辑待发送消息 1').click()
    expect(page.locator('.chat-image-input')).to_have_count(1)
    expect(page.locator('.chat-queue')).to_have_count(0)
    page.get_by_role('button', name='移除图片 1').click()
    page.locator('.chat-compose').evaluate('''(node, bytes) => {
        const data = new DataTransfer();
        data.items.add(new File([new Uint8Array(bytes)], 'drop.png', {type: 'image/png'}));
        node.dispatchEvent(new DragEvent('drop', {dataTransfer: data, bubbles: true, cancelable: true}));
    }''', list(PNG))
    expect(page.locator('.chat-image-input').get_by_role('status')).to_have_text('已上传')
    page.get_by_role('button', name='引导当前任务').click()
    expect(page.locator('.chat-user img')).to_be_visible()
    assert submitted(state, 'session.steer')[0]['resources'] == [RESOURCE]
    page.locator('input[type=file]').set_input_files({'name': 'vector.svg', 'mimeType': 'image/svg+xml', 'buffer': b'<svg/>'})
    expect(page.get_by_role('alert')).to_contain_text('仅支持 PNG')
    page.locator('input[type=file]').set_input_files({'name': 'large.png', 'mimeType': 'image/png', 'buffer': b'x' * (20 * 1024 * 1024 + 1)})
    expect(page.get_by_role('alert')).to_contain_text('不超过 20 MiB')


def test_rejected_submit_restores_image_and_draft(page):
    gateway(page, reject=True)
    uploads(page)
    pick(page)
    page.get_by_role('textbox', name='发送消息').fill('不要丢图')
    page.get_by_role('button', name='发送', exact=True).click()
    expect(page.get_by_role('alert')).to_contain_text('工作区不可用')
    expect(page.get_by_role('textbox', name='发送消息')).to_have_value('不要丢图')
    expect(page.locator('.chat-image-input')).to_have_count(1)


def test_unknown_submit_reuses_image_and_submission_id(page):
    state, _ = gateway(page, disconnect=True)
    uploads(page)
    pick(page)
    page.get_by_role('button', name='发送', exact=True).click()
    expect(page.get_by_role('button', name='重试发送', exact=True)).to_be_visible()
    expect(page.get_by_text('已连接', exact=True)).to_be_visible(timeout=15000)
    expect(page.get_by_role('button', name='重试发送', exact=True)).to_be_enabled()
    page.get_by_role('button', name='重试发送', exact=True).click()
    expect(page.locator('.chat-user img')).to_be_visible()
    assert len(submitted(state)) == 2
    assert submitted(state)[0]['submission_id'] == submitted(state)[1]['submission_id']
    assert submitted(state)[0]['resources'] == submitted(state)[1]['resources'] == [RESOURCE]


def test_history_image_edit_restages_asset_before_sending(page):
    records = [{'workspace_key': 'workspace-key', 'session_id': 'session-1', 'task_id': 'old-task',
                'type': 'user.message', 'payload': {'text': '', 'resources': [ASSET]}, 'sequence': 1}]
    calls = uploads(page)
    state, _ = gateway(page, records=records)
    expect(page.locator('.chat-user img')).to_be_visible()
    page.get_by_role('button', name='编辑为新消息').click()
    expect(page.locator('.chat-image-input').get_by_role('status')).to_have_text('已上传')
    assert calls == [PNG]
    page.get_by_role('button', name='发送', exact=True).click()
    expect(page.locator('.chat-user')).to_have_count(2)
    assert submitted(state)[0]['resources'] == [RESOURCE]
    assert submitted(state)[0]['prompt'] == ''


@pytest.mark.parametrize('prompt', ['', '解释这张图片'])
def test_ask_again_restores_image_prompt_and_restages_asset(page, prompt):
    records = [
        {'session_id': 'session-1', 'task_id': 'old-task', 'type': 'user.message',
         'payload': {'text': prompt, 'resources': [ASSET]}, 'sequence': 1},
        {'session_id': 'session-1', 'task_id': 'old-task', 'type': 'assistant.message',
         'payload': {'step': 1, 'text': '图片中的内容'}, 'sequence': 2},
    ]
    calls = uploads(page)
    state, _ = gateway(page, records=records)
    again = page.get_by_role('button', name='再次提问', exact=True)
    expect(again).to_be_enabled()
    again.click()
    expect(page.get_by_role('textbox', name='发送消息')).to_have_value(prompt)
    expect(page.locator('.chat-image-input').get_by_role('status')).to_have_text('已上传')
    assert calls == [PNG]
    page.get_by_role('button', name='发送', exact=True).click()
    expect(page.locator('.chat-user')).to_have_count(2)
    assert submitted(state)[0]['prompt'] == prompt
    assert submitted(state)[0]['resources'] == [RESOURCE]


@pytest.mark.parametrize('batch', [False, True])
def test_image_only_draft_blocks_current_session_deletion(page, batch):
    state, _ = gateway(page, sessions=[
        {'session_id': 'session-1', 'title': '当前会话'},
        {'session_id': 'session-2', 'title': '其他会话'},
    ])
    uploads(page)
    pick(page)
    open_sessions(page)
    if batch:
        page.get_by_role('button', name='多选', exact=True).click()
        page.get_by_role('checkbox', name='选择对话：当前会话', exact=True).click()
        delete = page.locator('.session-batch-actions').get_by_role('button', name='删除', exact=True)
    else:
        page.get_by_role('button', name='会话操作：当前会话', exact=True).click()
        delete = page.get_by_role('menuitem', name='删除会话：当前会话', exact=True)
    expect(delete).to_be_disabled()
    assert not submitted(state, 'session.delete')
    expect(page.locator('.chat-image-input')).to_have_count(1)
    if not batch:
        page.keyboard.press('Escape')
    page.get_by_role('button', name='移除图片 1', exact=True).click()
    if not batch:
        page.get_by_role('button', name='会话操作：当前会话', exact=True).click()
    expect(delete).to_be_enabled()
    delete.click()
    page.get_by_role('dialog').get_by_role('button', name='删除', exact=True).click()
    page.wait_for_url('**/*session=session-2')
    assert [call['session_id'] for call in submitted(state, 'session.delete')] == ['session-1']


def test_queued_image_dispatch_keeps_next_draft_image(page):
    state, emit = gateway(page)
    uploads(page)
    emit('task.started', {})
    pick(page)
    page.get_by_role('button', name='排队发送', exact=True).click()
    expect(page.locator('.chat-queue')).to_contain_text('1 张图片')
    pick(page)
    emit('task.finished', {'status': 'completed'})
    expect(page.locator('.chat-user img')).to_be_visible()
    expect(page.locator('.chat-queue')).to_have_count(0)
    expect(page.locator('.chat-image-input')).to_have_count(1)
    assert submitted(state)[0]['resources'] == [RESOURCE]


def test_removed_inflight_upload_cannot_restore_draft_or_submit(page):
    state, _ = gateway(page)
    pending = []
    page.route('**/resources', lambda route: pending.append(route))
    page.locator('input[type=file]').set_input_files({'name': 'waiting.png', 'mimeType': 'image/png', 'buffer': PNG})
    expect(page.locator('.chat-image-input')).to_have_count(1)
    expect(page.get_by_role('button', name='发送', exact=True)).to_be_disabled()
    page.get_by_role('button', name='移除图片 1').click()
    for route in pending:
        route.fulfill(json=RESOURCE)
    expect(page.locator('.chat-image-input')).to_have_count(0)
    assert not submitted(state)


def test_image_only_draft_protects_navigation_and_broken_history_is_readable(page):
    uploads(page)
    gateway(page)
    pick(page)
    follow_link(page, '记忆')
    expect(page.get_by_role('dialog')).to_be_visible()
    page.get_by_role('button', name='继续编辑', exact=True).click()
    expect(page.locator('.chat-image-input')).to_have_count(1)
    page.get_by_role('button', name='移除图片 1').click()
    page.route('**/resources*', lambda route: route.fulfill(status=404))
    records = [{'workspace_key': 'workspace-key', 'session_id': 'session-1', 'task_id': 'old-task',
                'type': 'user.message', 'payload': {'text': '', 'resources': [ASSET]}, 'sequence': 1}]
    gateway(page, records=records)
    expect(page.locator('.chat-image-error')).to_have_text('图片 1 无法加载')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


@pytest.mark.parametrize('width,height,expected', [
    (10, 1000, {'width': 60, 'height': 240}),
    (1000, 10, {'width': 240, 'height': 60}),
])
def test_extreme_image_ratios_keep_a_readable_thumbnail_above_text(page, width, height, expected):
    gateway(page)
    sample = base64.b64decode(page.evaluate('''([width, height]) => {
        const canvas = document.createElement('canvas');
        canvas.width = width; canvas.height = height;
        canvas.getContext('2d').fillRect(0, 0, width, height);
        return canvas.toDataURL('image/png').split(',')[1];
    }''', [width, height]))
    page.route('**/resources*', lambda route: route.fulfill(json=RESOURCE) if route.request.method == 'POST' else route.fulfill(content_type='image/png', body=sample))
    page.locator('input[type=file]').set_input_files({'name': 'long.png', 'mimeType': 'image/png', 'buffer': sample})
    expect(page.locator('.chat-image-input').get_by_role('status')).to_have_text('已上传')
    page.get_by_role('textbox', name='发送消息').fill('图片说明')
    page.get_by_role('button', name='发送', exact=True).click()
    tile = page.locator('.chat-user .chat-image-tile')
    expect(tile).to_be_visible()
    page.wait_for_function('''({width, height}) => {
        const tile = document.querySelector('.chat-user .chat-image-tile');
        return tile && tile.clientWidth === width && tile.clientHeight === height;
    }''', arg=expected)
    assert tile.locator('img').evaluate('node => getComputedStyle(node).objectFit') == 'cover'
    assert not tile.evaluate("node => node.closest('.bubble')")
    assert tile.bounding_box()['y'] + expected['height'] <= page.locator('.chat-user .bubble').bounding_box()['y']


@pytest.mark.parametrize('width', [390, 1440])
def test_large_image_preview_and_message_fit_viewport(page, width):
    page.set_viewport_size({'width': width, 'height': 900})
    gateway(page)
    sample = base64.b64decode(page.evaluate('''() => {
        const canvas = document.createElement('canvas');
        canvas.width = 960; canvas.height = 540;
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = '#e6eee9'; ctx.fillRect(0, 0, 960, 540);
        ctx.fillStyle = '#a45b70'; ctx.fillRect(60, 60, 360, 420);
        ctx.fillStyle = '#253d32'; ctx.font = '48px sans-serif';
        ctx.fillText('Icarus image', 460, 260);
        return canvas.toDataURL('image/png').split(',')[1];
    }'''))
    def handle(route):
        if route.request.method == 'POST':
            route.fulfill(json=RESOURCE)
        else:
            route.fulfill(content_type='image/png', body=sample)
    page.route('**/resources*', handle)
    page.locator('input[type=file]').set_input_files({'name': 'sample.png', 'mimeType': 'image/png', 'buffer': sample})
    expect(page.locator('.chat-image-input').get_by_role('status')).to_have_text('已上传')
    page.screenshot(path=f'test-results/image-input-{width}.png', full_page=True)
    page.get_by_role('textbox', name='发送消息').fill('这张图片里有什么？')
    page.get_by_role('button', name='发送', exact=True).click()
    image = page.locator('.chat-user img')
    expect(image).to_be_visible()
    page.wait_for_function('''() => { const image = document.querySelector('.chat-user img');
        return image && image.complete && image.naturalWidth === 960; }''')
    assert image.evaluate('image => image.getBoundingClientRect().width <= 280')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path=f'test-results/image-message-{width}.png', full_page=True)
    page.get_by_role('button', name='查看图片 1', exact=True).click()
    viewer = page.get_by_role('dialog', name='图片查看器', exact=True)
    expect(viewer).to_be_visible()
    picture = viewer.locator('.chat-image-viewer-stage > img')
    expect(picture).to_be_visible()
    assert picture.evaluate('node => node.clientHeight <= node.parentElement.clientHeight && node.clientWidth <= node.parentElement.clientWidth')
    page.screenshot(path=f'test-results/image-viewer-real-{width}.png')
    page.keyboard.press('Escape')


@pytest.mark.parametrize('width', [390, 1440])
def test_image_geometry_preview_zoom_navigation_and_close(page, width):
    page.set_viewport_size({'width': width, 'height': 900})
    state, _ = gateway(page)
    uploads(page)
    pick(page)
    draft = page.locator('.chat-image-input')
    expect(draft).to_have_css('border-radius', '16px')
    assert draft.bounding_box()['width'] == draft.bounding_box()['height'] == 64
    draft.hover()
    remove = page.get_by_role('button', name='移除图片 1', exact=True)
    expect(remove).to_be_visible()
    bounds = remove.bounding_box()
    box = draft.bounding_box()
    assert bounds['width'] == bounds['height'] == 18
    assert bounds['y'] == box['y'] + 4
    assert bounds['x'] + bounds['width'] == box['x'] + box['width'] - 4
    page.get_by_role('button', name='预览待发送图片 1').click()
    viewer = page.get_by_role('dialog', name='图片查看器', exact=True)
    expect(viewer).to_be_visible()
    page.keyboard.press('Escape')
    expect(viewer).to_have_count(0)
    assert not submitted(state)
    page.locator('input[type=file]').set_input_files({'name': 'second.png', 'mimeType': 'image/png', 'buffer': PNG})
    expect(page.locator('.chat-image-input')).to_have_count(2)
    assert page.locator('.chat-image-input').evaluate_all('nodes => nodes.every(node => node.clientWidth === 64 && node.clientHeight === 64)')
    page.get_by_role('button', name='移除图片 2', exact=True).click()
    expect(page.locator('.chat-image-input')).to_have_count(1)
    assert draft.bounding_box()['width'] == draft.bounding_box()['height'] == 64
    page.get_by_role('button', name='发送', exact=True).click()
    tile = page.locator('.chat-user .chat-image-tile')
    expect(tile).to_be_visible()
    assert tile.bounding_box()['width'] == 240
    page.get_by_role('button', name='查看图片 1', exact=True).click()
    expect(viewer).to_be_visible()
    page.get_by_role('button', name='放大图片', exact=True).click()
    expect(page.get_by_role('button', name='还原图片')).to_have_text('125%')
    page.get_by_role('button', name='还原图片').click()
    expect(page.get_by_role('button', name='还原图片')).to_have_text('100%')
    picture = viewer.locator('.chat-image-viewer-stage > img')
    picture.click()
    expect(page.get_by_role('button', name='还原图片')).to_have_text('200%')
    picture.click()
    expect(page.get_by_role('button', name='还原图片')).to_have_text('100%')
    stage = viewer.locator('.chat-image-viewer-stage')
    stage.hover()
    page.mouse.wheel(0, -200)
    expect(stage).to_have_attribute('data-zoomed', 'true')
    page.screenshot(path=f'test-results/image-viewer-{width}.png')
    page.keyboard.press('Escape')
    expect(viewer).to_have_count(0)
    expect(page.get_by_role('button', name='查看图片 1', exact=True)).to_be_focused()

    # Two uploaded images render as compact squares, including history replay.
    resources = [RESOURCE, ASSET]
    records = [{'workspace_key': 'workspace-key', 'session_id': 'session-1', 'task_id': 'old-task',
                'type': 'user.message', 'payload': {'text': '', 'resources': resources}, 'sequence': 1}]
    gateway(page, records=records)
    expect(page.locator('.chat-user img')).to_have_count(2)
    assert all(box == {'width': 64, 'height': 64} for box in page.locator('.chat-user .chat-image-tile').evaluate_all('nodes => nodes.map(node => ({width: node.clientWidth, height: node.clientHeight}))'))
    page.get_by_role('button', name='查看图片 1', exact=True).click()
    expect(page.get_by_role('button', name='上一张图片')).to_be_disabled()
    page.keyboard.press('ArrowRight')
    expect(viewer.locator('.chat-image-viewer-toolbar')).to_contain_text('2 / 2')
    expect(page.get_by_role('button', name='下一张图片')).to_be_disabled()
    page.keyboard.press('ArrowLeft')
    expect(viewer.locator('.chat-image-viewer-toolbar')).to_contain_text('1 / 2')
    page.get_by_role('button', name='关闭图片查看器').click()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
