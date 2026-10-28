from playwright.sync_api import expect


def test_create_circle_and_member_without_invented_totals(page, rela):
    page.get_by_role('navigation', name='人物分类').get_by_role('link', name='圈子', exact=True).click()
    page.get_by_role('button', name='新建圈子', exact=True).click()
    form = page.get_by_role('dialog', name='新建圈子', exact=True)
    form.get_by_role('textbox', name='圈子名称', exact=True).fill('读书会')
    form.get_by_role('textbox', name='圈子类型', exact=True).fill('interest')
    form.get_by_role('button', name='保存', exact=True).click()
    dialog = page.get_by_role('dialog', name='圈子详情', exact=True)
    expect(dialog.get_by_role('heading', name='读书会', exact=True)).to_be_visible()
    dialog.get_by_role('button', name='添加成员', exact=True).click()
    form = page.get_by_role('dialog', name='添加成员', exact=True)
    form.get_by_role('button', name='选择成员人物：王明，p_000002_alpha', exact=True).click()
    form.get_by_role('textbox', name='圈内别名', exact=True).fill('领读人')
    form.get_by_role('button', name='保存', exact=True).click()
    expect(dialog.get_by_text('领读人', exact=False)).to_be_visible()
    dialog.get_by_role('button', name='退出圈子：王明，p_000002_alpha', exact=True).click()
    page.get_by_role('dialog', name='退出圈子', exact=True).get_by_role('button', name='确认退出', exact=True).click()
    expect(dialog.get_by_text('暂无成员', exact=True)).to_be_visible()


def test_circle_deep_link_resolves_beyond_first_page(page, rela):
    rela['circles'] = [dict(rela['circles'][0], circle_id=f'c_{i}', name=f'圈子{i}') for i in range(205)]
    page.goto(page.url.split('#')[0] + '#/people/circles?circle=c_204')
    dialog = page.get_by_role('dialog', name='圈子详情', exact=True)
    expect(dialog.get_by_role('heading', name='圈子204', exact=True)).to_be_visible()
    assert any(path == '/v1/circles' and q.get('cursor') == '200' for _, path, q, _ in rela['calls'])
    assert not any(path == '/v1/circles/c_204' for _, path, _, _ in rela['calls'])


def test_circle_owner_clear_member_edit_and_left_filter(page, rela):
    rela['circles'][0]['owner_person_id'] = rela['people'][0]['person_id']
    rela['members'] = [{
        'circle_id': 'c_work', 'person_id': 'p_000002_alpha', 'person': rela['people'][1],
        'alias': '旧称呼', 'role': 'member', 'identity_label': None, 'status': 'active',
        'joined_at': '2026-10-20T00:00:00Z',
    }]
    page.get_by_role('navigation', name='人物分类').get_by_role('link', name='圈子', exact=True).click()
    page.locator('.rela-circle-row').first.click()
    dialog = page.get_by_role('dialog', name='圈子详情', exact=True)
    dialog.get_by_role('button', name='编辑圈子', exact=True).click()
    form = page.get_by_role('dialog', name='编辑圈子', exact=True)
    expect(form.get_by_text('王明 · 人物', exact=True)).to_be_visible()
    form.get_by_role('button', name='不指定负责人', exact=True).click()
    form.get_by_role('button', name='保存', exact=True).click()
    expect(dialog.get_by_text('负责人：未指定', exact=True)).to_be_visible()
    assert rela['circles'][0]['owner_person_id'] is None
    dialog.get_by_role('button', name='编辑成员：王明，p_000002_alpha', exact=True).click()
    form = page.get_by_role('dialog', name='编辑成员', exact=True)
    form.get_by_role('textbox', name='圈内别名', exact=True).fill('新称呼')
    form.get_by_role('textbox', name='成员角色', exact=True).fill('host')
    form.get_by_role('textbox', name='成员身份说明', exact=True).fill('协调人')
    form.get_by_role('button', name='保存', exact=True).click()
    expect(dialog.get_by_text('host', exact=True)).to_be_visible()
    assert rela['members'][0]['joined_at'] == '2026-10-20T00:00:00Z'
    dialog.get_by_role('button', name='退出圈子：王明，p_000002_alpha', exact=True).click()
    page.get_by_role('dialog', name='退出圈子', exact=True).get_by_role('button', name='确认退出', exact=True).click()
    expect(dialog.get_by_text('暂无成员', exact=True)).to_be_visible()
    dialog.get_by_text('包含已退出', exact=True).click()
    expect(dialog.get_by_text('已退出', exact=True)).to_be_visible()
    dialog.get_by_role('link', name='查看成员人物：王明，p_000002_alpha', exact=True).click()
    expect(page.get_by_role('dialog', name='人物档案', exact=True).get_by_text('p_000002_alpha', exact=True)).to_be_visible()
    page.get_by_role('button', name='关闭人物档案', exact=True).click()
    expect(dialog.get_by_role('heading', name='产品组', exact=True)).to_be_visible()
