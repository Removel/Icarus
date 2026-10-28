from playwright.sync_api import expect


def test_create_person_and_edit_alias(page, rela):
    page.get_by_role('button', name='添加人物', exact=True).click()
    form = page.get_by_role('dialog', name='添加人物', exact=True)
    form.get_by_role('textbox', name='姓名', exact=True).fill('新朋友')
    form.get_by_role('button', name='保存', exact=True).click()
    expect(form).not_to_be_visible()
    expect(page.get_by_role('dialog', name='人物档案', exact=True)).to_be_visible()
    expect(page.get_by_role('heading', name='新朋友', exact=True)).to_be_visible()
    page.get_by_role('button', name='编辑基础信息', exact=True).click()
    form = page.get_by_role('dialog', name='编辑人物', exact=True)
    form.get_by_role('textbox', name='姓名', exact=True).fill('修改姓名')
    form.get_by_role('button', name='保存', exact=True).click()
    expect(page.get_by_role('heading', name='修改姓名', exact=True)).to_be_visible()
    page.get_by_role('tab', name='别名与身份核对', exact=True).click()
    page.get_by_role('button', name='添加别名', exact=True).click()
    form = page.get_by_role('dialog', name='添加别名', exact=True)
    form.get_by_role('textbox', name='别名值', exact=True).fill('新昵称')
    form.get_by_role('button', name='保存', exact=True).click()
    expect(page.locator('.rela-alias-list').get_by_text('新昵称', exact=True)).to_be_visible()
    assert any(method == 'POST' and path.endswith('/aliases') and body['namespace'] == 'name' for method, path, _, body in rela['calls'])


def test_write_result_unknown_is_not_automatically_retried(page, rela):
    rela['abort_writes'] = True
    page.get_by_role('button', name='添加人物', exact=True).click()
    form = page.get_by_role('dialog', name='添加人物', exact=True)
    form.get_by_role('textbox', name='姓名', exact=True).fill('保留草稿')
    form.get_by_role('button', name='保存', exact=True).click()
    expect(form.get_by_role('alert')).to_contain_text('结果待核对')
    expect(form.get_by_role('textbox', name='姓名', exact=True)).to_have_value('保留草稿')
    expect(form.get_by_role('button', name='保存', exact=True)).to_be_disabled()
    assert sum(method == 'POST' for method, _, _, _ in rela['calls']) == 1


def test_conflict_keeps_draft_and_escape_requires_discard(page, rela):
    page.locator('.person-card').first.click()
    page.get_by_role('tab', name='别名与身份核对', exact=True).click()
    page.get_by_role('button', name='添加别名', exact=True).click()
    form = page.get_by_role('dialog', name='添加别名', exact=True)
    form.get_by_role('textbox', name='别名命名空间', exact=True).fill('qq')
    form.get_by_role('textbox', name='别名值', exact=True).fill('conflicting-id')
    rela['fail']['/v1/persons/p_000001_alpha/aliases'] = 409
    form.get_by_role('button', name='保存', exact=True).click()
    expect(form.get_by_role('alert')).to_contain_text('冲突')
    expect(form.get_by_role('textbox', name='别名值', exact=True)).to_have_value('conflicting-id')
    form.get_by_role('button', name='取消', exact=True).click()
    confirmation = page.get_by_role('dialog', name='放弃未保存的更改？', exact=True)
    expect(confirmation).to_be_visible()
    confirmation.get_by_role('button', name='继续编辑', exact=True).click()
    expect(form).to_be_visible()
    form.press('Escape')
    expect(confirmation).to_be_visible()
    confirmation.get_by_role('button', name='放弃', exact=True).click()
    expect(form).not_to_be_visible()
    expect(page.get_by_role('dialog', name='人物档案', exact=True)).to_be_visible()
