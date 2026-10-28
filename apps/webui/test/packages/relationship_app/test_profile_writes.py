from playwright.sync_api import expect


def test_json_null_correction_and_tombstone(page, rela):
    page.locator('.person-card').first.click()
    page.get_by_role('button', name='添加档案', exact=True).click()
    form = page.get_by_role('dialog', name='添加档案字段', exact=True)
    form.get_by_role('textbox', name='档案 key', exact=True).fill('custom.key')
    form.get_by_role('combobox', name='值类型', exact=True).click()
    page.get_by_role('option', name='JSON', exact=True).click()
    form.get_by_role('textbox', name='档案值', exact=True).fill('null')
    form.get_by_role('button', name='保存', exact=True).click()
    field = page.locator('.rela-profile-field').filter(has=page.get_by_text('custom.key', exact=True))
    expect(field.get_by_text('null', exact=True)).to_be_visible()
    field.get_by_role('button', name='忘记此字段', exact=True).click()
    confirmation = page.get_by_role('dialog', name='忘记档案字段', exact=True)
    confirmation.get_by_role('button', name='确认忘记', exact=True).click()
    expect(field).to_have_count(0)
    history = page.locator('.rela-field-history')
    expect(history).to_contain_text('字段历史：custom.key')
    expect(history.get_by_text('已忘记', exact=False)).to_be_visible()
    assert rela['history']['custom.key'][0]['status'] == 'deleted'
    put = next(body for method, path, _, body in rela['calls'] if method == 'PUT' and path.endswith('/custom.key'))
    assert put == {'value': None, 'source': 'user'}
    page.reload()
    page.get_by_text('按 key 查看字段历史（含已忘记字段）', exact=True).click()
    page.get_by_role('textbox', name='历史字段 key', exact=True).fill('custom.key')
    page.get_by_role('button', name='读取字段历史', exact=True).click()
    expect(history.get_by_text('deleted', exact=True)).to_be_visible()


def test_invalid_json_and_service_validation_preserve_input(page, rela):
    page.locator('.person-card').first.click()
    page.get_by_role('button', name='添加档案', exact=True).click()
    form = page.get_by_role('dialog', name='添加档案字段', exact=True)
    form.get_by_role('textbox', name='档案 key', exact=True).fill('custom.value')
    form.get_by_role('combobox', name='值类型', exact=True).click()
    page.get_by_role('option', name='JSON', exact=True).click()
    value = form.get_by_role('textbox', name='档案值', exact=True)
    value.fill('{invalid')
    form.get_by_role('button', name='保存', exact=True).click()
    expect(form.get_by_role('alert')).to_contain_text('合法 JSON')
    assert not any(method == 'PUT' for method, _, _, _ in rela['calls'])
    rela['fail']['/v1/persons/p_000001_alpha/profile/custom.value'] = 422
    value.fill('{"valid": true}')
    form.get_by_role('button', name='保存', exact=True).click()
    expect(form.get_by_role('alert')).to_contain_text('检查输入')
    expect(value).to_have_value('{"valid": true}')
    expect(form.get_by_role('button', name='保存', exact=True)).to_be_enabled()
