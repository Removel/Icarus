"""Stateful Mem0 HTTP contract fixture; data survives browser reloads."""

from datetime import datetime, timezone
from urllib.parse import urlsplit, unquote, parse_qs
from uuid import uuid4


def fake_mem0(page):
    contents = [
        ('更喜欢简洁、克制的界面，信息层级清晰，操作尽量直接。', '设计偏好', 'workspace:1234567890abcdef', 'lin', None),
        ('项目采用 Monorepo 结构，各应用保持独立，共享能力按需提取。', '项目约定', 'workspace:1234567890abcdef', 'lin', None),
        ('默认使用中文交流，技术名词可以保留英文。', '沟通习惯', 'global', 'lin', None),
        ('前端组件优先使用 Semi Design，保持一致的交互和视觉规范。', '技术选择', 'workspace:1234567890abcdef', 'lin', None),
        ('回答复杂问题时，先给出结论，再补充必要的解释。', '沟通习惯', 'global', 'chen', None),
        ('每周五整理本周的设计反馈，汇总到项目知识库。', '工作习惯', 'workspace:1234567890abcdef', 'chen', None),
        ('每天晚上九点整理当天的项目笔记。', '工作习惯', 'workspace:1234567890abcdef', 'lin', '1970-01-01'),
        ('本轮原型评审安排在九月第一周。', '项目安排', 'workspace:1234567890abcdef', 'lin', '2026-09-07'),
    ]
    ids = ['a8f2c1', 'b6d4e9', 'c3a7f5', 'd9e2b8', 'e5f1a3', 'f2b8d6', 'g4c9e1', 'h7a3f2']
    rows = {}
    histories = {}
    calls = []
    for i, (memory, category, run_id, user_id, expiration) in enumerate(contents):
        row = dict(id='mem_' + ids[i], memory=memory, metadata={'category': category, 'custom': 'preserve'},
                   run_id=run_id, user_id=user_id, expiration_date=expiration, agent_id='icarus',
                   updated_at=f'2026-09-{28 - i // 2}T{10 - i % 2:02}:42:00+08:00', created_at='2026-09-24T09:12:00+08:00')
        rows[row['id']] = row
        histories[row['id']] = [dict(event='ADD', new_memory=memory, created_at=row['created_at'])]
        if i == 0:
            histories[row['id']].append(dict(event='UPDATE', new_memory=memory, created_at=row['updated_at']))

    def respond(route):
        request = route.request
        path = unquote(urlsplit(request.url).path.removeprefix('/api/mem0'))
        body = request.post_data_json if request.post_data else {}
        calls.append((request.method, path, body, request.url))
        now = datetime.now(timezone.utc).isoformat()
        if path == '/memories/page' and request.method == 'GET':
            params = {k: v[0] for k, v in parse_qs(urlsplit(request.url).query).items()}
            all_rows = list(rows.values())
            today = datetime.now(timezone.utc).date().isoformat()
            expired = lambda row: bool(row.get('expiration_date') and row['expiration_date'] < today)
            category = lambda row: row.get('metadata', {}).get('category') or '未分类'
            state = params.get('state', 'all')
            matches = [row for row in all_rows
                       if (state == 'all' or expired(row) == (state == 'expired'))
                       and all(not params.get(key) or row.get(key) == params[key] for key in ('user_id', 'run_id'))
                       and (not params.get('category') or category(row) == params['category'])
                       and params.get('query', '').strip().lower() in f"{row['memory']} {category(row)} {row.get('user_id', '')}".lower()]
            matches.sort(key=lambda row: row.get('updated_at', ''), reverse=params.get('descending', 'true') == 'true')
            size = int(params.get('page_size', 12))
            current = min(int(params.get('page', 1)), max(1, (len(matches) + size - 1) // size))
            active = sum(not expired(row) for row in all_rows)
            result = dict(results=matches[(current - 1) * size:current * size], page=current, page_size=size,
                          total=len(matches), counts=dict(all=len(all_rows), active=active, expired=len(all_rows) - active),
                          categories=sorted({category(row) for row in all_rows}),
                          users=sorted({row.get('user_id', '') for row in all_rows}),
                          scopes=sorted({row.get('run_id', '') for row in all_rows}), truncated=False)
        elif path == '/memories' and request.method == 'GET':
            result = {'results': list(rows.values())}
        elif path == '/memories' and request.method == 'POST':
            assert body['infer'] is False
            id = str(uuid4())
            rows[id] = {k: v for k, v in body.items() if k not in ('messages', 'infer')}
            rows[id].update(id=id, memory=body['messages'][0]['content'], created_at=now, updated_at=now)
            histories[id] = [dict(event='ADD', new_memory=rows[id]['memory'], created_at=now)]
            result = {'results': [{'id': id, 'event': 'ADD', 'memory': rows[id]['memory']}]}
        else:
            id = path.split('/')[2]
            if id not in rows:
                route.fulfill(body="null", content_type="application/json")
                return
            if path.endswith('/history'):
                result = histories.get(id, [])
            elif request.method == 'GET':
                result = rows[id]
            elif request.method == 'PUT':
                for key, value in body.items():
                    rows[id]['memory' if key == 'text' else key] = value
                rows[id]['updated_at'] = now
                histories[id].append(dict(event='UPDATE', new_memory=rows[id]['memory'], created_at=now))
                result = {'message': 'Memory updated successfully'}
            elif request.method == 'DELETE':
                del rows[id]
                result = {'message': 'Memory deleted successfully'}
            else:
                raise AssertionError((request.method, path))
        route.fulfill(json=result)

    page.route('**/api/mem0/**', respond)
    page.mem0_calls = calls
    return rows
