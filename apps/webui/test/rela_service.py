"""Isolated REST-shaped relationship fixtures. Never calls a database or a model."""
from copy import deepcopy
from urllib.parse import urlsplit, parse_qs, unquote


def fake_rela(page):
    people = [
        {'person_id': 'p_000001_alpha', 'display_name': '王明', 'kind': 'human', 'status': 'active', 'aliases': [{'alias_id': 'a_1', 'namespace': 'name', 'value': '小王'}]},
        {'person_id': 'p_000002_alpha', 'display_name': '王明', 'kind': 'virtual', 'status': 'active', 'aliases': [{'alias_id': 'a_2', 'namespace': 'name', 'value': '阿明'}]},
        {'person_id': 'p_archived', 'display_name': '归档人物', 'kind': 'human', 'status': 'archived', 'aliases': []},
    ]
    for index in range(3, 24):
        people.append({'person_id': f'p_{index:06}_alpha', 'display_name': f'朋友{index}', 'kind': 'human', 'status': 'active', 'aliases': []})
    profiles = [{'id': 1, 'attr_key': 'open.unknown', 'attr_value': {'food': ['咖啡', '茶']}, 'source': 'agent', 'source_ref': 'ref:plain', 'recorded_at': '2026-10-20T10:00:00Z', 'status': 'active'},
                {'id': 2, 'attr_key': 'valid_null', 'attr_value': None, 'source': 'user', 'source_ref': None, 'recorded_at': '2026-10-21T10:00:00Z', 'status': 'active'}]
    circle = {'circle_id': 'c_work', 'name': '产品组', 'kind': 'work', 'status': 'active', 'owner_person_id': None}
    relation = {'relation_id': 'r_work', 'from_person_id': people[0]['person_id'], 'to_person_id': people[1]['person_id'], 'direction': 'one_way', 'kind': 'work', 'forward_detail': '项目负责人', 'reverse_detail': None, 'status': 'active', 'source': 'user', 'scope_circle_id': None}
    state = {'people': people, 'profiles': profiles, 'circles': [circle], 'relations': [relation], 'calls': [], 'fail': {}, 'members': [], 'history': {p['attr_key']: [deepcopy(p)] for p in profiles}}

    def respond(route):
        url = urlsplit(route.request.url)
        path = unquote(url.path.removeprefix('/api/rela'))
        query = {key: values[0] for key, values in parse_qs(url.query).items()}
        method = route.request.method
        body = route.request.post_data_json if method in ('POST', 'PUT', 'PATCH') else None
        state['calls'].append((method, path, query, body))
        failure = state['fail'].get(path)
        if failure:
            route.fulfill(status=failure, json={'error': {'code': 'UNAVAILABLE', 'message': 'fixture failure'}})
            return
        if method != 'GET' and state.get('abort_writes'):
            route.abort('failed')
            return
        def send(value, status=200):
            route.fulfill(status=status, json=value)
        def paged(items):
            if query.get('cursor') == 'invalid':
                return send({'error': {'code': 'VALIDATION_ERROR', 'message': 'invalid cursor'}}, 400)
            offset = int(query.get('cursor', '0'))
            size = int(query.get('limit', '20'))
            return send({'items': items[offset:offset + size], 'has_more': offset + size < len(items), 'next_cursor': str(offset + size) if offset + size < len(items) else None})
        archived = query.get('include_archived') == 'true'
        if path == '/v1/persons' and method == 'POST':
            new = {'person_id': f'p_new_{len(people)}', 'display_name': body['display_name'], 'kind': body['kind'], 'status': 'active', 'aliases': []}
            people.append(new)
            return send({'person': new, 'created': True}, 201)
        if path == '/v1/persons' and method == 'GET':
            rows = [p for p in people if archived or p['status'] == 'active']
            if query.get('kind'):
                rows = [p for p in rows if p['kind'] == query['kind']]
            text = query.get('query', '').lower()
            return paged([p for p in rows if text in (p['display_name'] + ' '.join(a['value'] for a in p['aliases'])).lower()])
        if path == '/v1/identity/resolve':
            return send({'person': next((p for p in people if any(a['namespace'] == query.get('namespace') and a['value'] == query.get('value') for a in p['aliases'])), None)})
        if path == '/v1/circles':
            if method == 'POST':
                item = body | {'circle_id': f'c_new_{len(state["circles"])}', 'status': 'active'}
                state['circles'].append(item)
                return send({'circle': item}, 201)
            return paged([c for c in state['circles'] if query.get('query', '') in c['name'] and (archived or c['status'] == 'active') and (not query.get('kind') or query['kind'] == c['kind'])])
        if path == '/v1/relations':
            if method == 'POST':
                item = body | {'relation_id': 'r_new', 'status': 'active', 'reverse_detail': body.get('reverse_detail')}
                state['relations'].append(item)
                return send({'relation': item, 'created': True})
            pid = query.get('person_id')
            rows = []
            for item in state['relations']:
                if item['status'] != query.get('status', 'active') or pid not in (item['from_person_id'], item['to_person_id']):
                    continue
                outgoing = item['from_person_id'] == pid
                other = next(p for p in people if p['person_id'] == item['to_person_id' if outgoing else 'from_person_id'])
                detail = item['reverse_detail'] if outgoing else item['forward_detail']
                rows.append(item | {'other_person': other, 'relative_direction': 'outgoing' if outgoing else 'incoming', 'detail': detail, 'detail_missing': detail is None})
            return paged(rows)
        parts = path.strip('/').split('/')
        if parts[:2] == ['v1', 'relations'] and len(parts) == 3:
            item = next(r for r in state['relations'] if r['relation_id'] == parts[2])
            if method == 'PATCH':
                item.update(body)
                return send({'relation': item})
            if method == 'DELETE':
                item['status'] = 'invalid'
                return route.fulfill(status=204)
        if parts[:2] == ['v1', 'circles'] and len(parts) >= 3:
            item = next((c for c in state['circles'] if c['circle_id'] == parts[2]), None)
            if len(parts) == 3 and method == 'PATCH':
                item.update(body)
                return send({'circle': item})
            if len(parts) == 5 and parts[3] == 'members':
                pid = parts[4]
                previous = next((m for m in state['members'] if m['person_id'] == pid and m.get('circle_id') == parts[2]), None)
                state['members'] = [m for m in state['members'] if m is not previous]
                if method == 'PUT':
                    member = body | {'person_id': pid, 'circle_id': parts[2], 'person': next(p for p in people if p['person_id'] == pid), 'status': 'active'}
                    state['members'].append(member)
                    return send({'membership': member})
                if previous:
                    state['members'].append(previous | {'status': 'left'})
                return route.fulfill(status=204)
        if len(parts) >= 3 and parts[:2] == ['v1', 'persons']:
            person = next((p for p in people if p['person_id'] == parts[2]), None)
            if not person or person['status'] == 'archived' and not archived:
                return send({'error': {'code': 'NOT_FOUND', 'message': 'person not found'}}, 404)
            if len(parts) == 3:
                if method == 'PATCH':
                    person.update(body)
                return send({'person': person})
            if parts[3] == 'aliases':
                if method == 'POST':
                    alias = body | {'alias_id': f'a_{len(person["aliases"])}_new'}
                    person['aliases'].append(alias)
                    return send({'alias': alias}, 201)
                if method == 'DELETE':
                    person['aliases'] = [a for a in person['aliases'] if a['alias_id'] != parts[4]]
                    return route.fulfill(status=204)
            if parts[3] == 'profile':
                if method in ('PUT', 'DELETE'):
                    key = parts[4]
                    field = {'id': 10 + len(state['history'].get(key, [])), 'attr_key': key, 'attr_value': body['value'] if body else None, 'source': 'user', 'source_ref': None, 'recorded_at': '2026-10-27T10:00:00Z', 'status': 'deleted' if method == 'DELETE' else 'active'}
                    state['profiles'] = [p for p in state['profiles'] if p['attr_key'] != key]
                    if method == 'PUT':
                        state['profiles'].append(field)
                    state['history'].setdefault(key, []).insert(0, field)
                    return route.fulfill(status=204) if method == 'DELETE' else send({'entry': field})
                if len(parts) == 6 and parts[5] == 'history':
                    return paged(state['history'].get(parts[4], []))
                if len(parts) == 5 and parts[4] == 'keys':
                    return paged([p['attr_key'] for p in state['profiles']])
                return paged([p for p in state['profiles'] if query.get('q', '') in p['attr_key'] + str(p['attr_value'])])
            if parts[3] == 'circles':
                return paged([{'circle_id': circle['circle_id'], 'circle': circle, 'role': 'member', 'status': 'active'}])
        if path.endswith('/members'):
            return paged([m for m in state['members'] if m.get('circle_id') == parts[2] and (query.get('include_left') == 'true' or m['status'] == 'active') and (archived or m['person']['status'] == 'active')])
        raise AssertionError(f'Unhandled relationship fixture: {method} {path}')

    page.route('**/api/rela/**', respond)
    return state
