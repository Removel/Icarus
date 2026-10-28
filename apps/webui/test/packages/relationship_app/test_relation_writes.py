from playwright.sync_api import expect


def test_relation_requires_real_candidate_and_both_descriptions(page, rela):
    page.locator('.person-card').first.click()
    page.get_by_role('tab', name='关系', exact=True).click()
    page.get_by_role('button', name='添加关系', exact=True).click()
    form = page.get_by_role('dialog', name='添加关系', exact=True)
    form.get_by_role('button', name='选择关联人物：王明，p_000002_alpha', exact=True).click()
    form.get_by_role('textbox', name='关系类型', exact=True).fill('friend')
    form.get_by_role('combobox', name='关系方向', exact=True).click()
    page.get_by_role('option', name='双向称谓', exact=True).click()
    form.get_by_role('textbox', name='正向称谓', exact=True).fill('同事')
    form.get_by_role('textbox', name='反向称谓', exact=True).fill('同事')
    form.get_by_role('button', name='保存', exact=True).click()
    expect(form).not_to_be_visible()
    body = next(body for method, path, _, body in rela['calls'] if method == 'POST' and path == '/v1/relations')
    assert body['from_person_id'] == 'p_000001_alpha' and body['to_person_id'] == 'p_000002_alpha'
    assert body['direction'] == 'two_way'
    assert body['forward_detail'] == body['reverse_detail'] == '同事'
    item = page.locator('.rela-relation').filter(has_text='项目负责人')
    item.get_by_role('button', name='失效关系', exact=True).click()
    page.get_by_role('dialog', name='失效关系', exact=True).get_by_role('button', name='确认失效', exact=True).click()
    expect(item).to_have_count(0)


def test_normalized_two_way_patch_keeps_endpoints_and_scope(page, rela):
    relation = rela['relations'][0]
    relation.update(from_person_id='p_000002_alpha', to_person_id='p_000001_alpha', direction='two_way', forward_detail='导师', reverse_detail='学生', scope_circle_id='c_work')
    page.locator('.person-card').first.click()
    page.get_by_role('tab', name='关系', exact=True).click()
    expect(page.get_by_text('王明是王明的导师', exact=True)).to_be_visible()
    page.get_by_role('button', name='修改关系', exact=True).click()
    form = page.get_by_role('dialog', name='修改关系', exact=True)
    form.get_by_role('textbox', name='正向称谓', exact=True).fill('指导者')
    form.get_by_role('textbox', name='反向称谓', exact=True).fill('学习者')
    form.get_by_role('button', name='保存', exact=True).click()
    expect(form).not_to_be_visible()
    patch = next(body for method, path, _, body in rela['calls'] if method == 'PATCH' and path == '/v1/relations/r_work')
    assert patch['forward_detail'] == '指导者' and patch['reverse_detail'] == '学习者'
    assert 'from_person_id' not in patch and 'to_person_id' not in patch and 'scope_circle_id' not in patch
    assert relation['from_person_id'] == 'p_000002_alpha' and relation['scope_circle_id'] == 'c_work'
    expect(page.get_by_text('王明是王明的指导者', exact=True)).to_be_visible()
