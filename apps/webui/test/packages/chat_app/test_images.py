import base64
import re

import pytest

from playwright.sync_api import expect
from test.packages.chat_app.test_chat import gateway


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
    page.get_by_role('link', name='记忆', exact=True).first.click()
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
