from playwright.sync_api import expect


def test_reading_detail_json_history_relations_and_return(page, rela):
    expect(page.locator('.relationship-page')).not_to_be_focused()
    page.locator('.person-card').first.click()
    dialog = page.get_by_role('dialog', name='人物档案', exact=True)
    expect(dialog.get_by_text('open.unknown', exact=True)).to_be_visible()
    expect(dialog.get_by_text('null', exact=True)).to_be_visible()
    expect(dialog.get_by_text('ref:plain', exact=False)).to_be_visible()
    dialog.get_by_role('button', name='查看 valid_null 的历史', exact=True).click()
    expect(dialog.get_by_text('字段历史：valid_null', exact=True)).to_be_visible()
    dialog.get_by_role('tab', name='关系', exact=True).click()
    expect(dialog.get_by_text('王明是王明的项目负责人', exact=True)).to_be_visible()
    expect(dialog.get_by_text('未提供反向称谓', exact=False)).to_be_visible()
    dialog.get_by_role('link', name='查看关联人物：王明，p_000002_alpha', exact=True).click()
    expect(dialog.get_by_text('p_000002_alpha', exact=True)).to_be_visible()
    page.go_back()
    expect(dialog.get_by_text('p_000001_alpha', exact=True)).to_be_visible()
    dialog.get_by_role('button', name='关闭人物档案', exact=True).click()
    expect(dialog).not_to_be_visible()
    expect(page.locator('.person-card').first).to_be_focused()


def test_deep_link_refresh_and_identity_not_found(page, rela):
    page.goto(page.url.split('#')[0] + '#/people?person=p_000002_alpha&section=identity')
    dialog = page.get_by_role('dialog', name='人物档案', exact=True)
    expect(dialog.get_by_text('阿明', exact=True)).to_be_visible()
    dialog.get_by_role('textbox', name='身份命名空间', exact=True).fill('qq')
    dialog.get_by_role('textbox', name='精确身份值', exact=True).fill('missing')
    dialog.get_by_role('button', name='核对身份', exact=True).click()
    expect(dialog.get_by_text('未找到对应人物', exact=True)).to_be_visible()
    page.reload()
    expect(dialog.get_by_role('tab', name='别名与身份核对', exact=True)).to_have_attribute('aria-selected', 'true')
    dialog.get_by_role('button', name='关闭人物档案', exact=True).click()
    expect(page.locator('.person-card')).to_have_count(20)


def test_section_failure_does_not_hide_person(page, rela):
    rela['fail']['/v1/persons/p_000001_alpha/profile'] = 503
    page.locator('.person-card').first.click()
    dialog = page.get_by_role('dialog', name='人物档案', exact=True)
    expect(dialog.get_by_role('alert')).to_be_visible()
    expect(dialog.get_by_role('heading', name='王明', exact=True)).to_be_visible()
    dialog.get_by_role('tab', name='圈子', exact=True).click()
    expect(dialog.get_by_text('产品组', exact=True)).to_be_visible()
    assert not any(path.startswith('/v1/persons/p_000001_alpha/profile/') and method != 'GET' for method, path, _, _ in rela['calls'])
