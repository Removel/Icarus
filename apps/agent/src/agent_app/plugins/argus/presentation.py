"""Small model-facing views. Transport envelopes remain runtime-owned."""
import json


def _dict(value):
    return value.model_dump() if hasattr(value, 'model_dump') else value


def source_view(item):
    item = _dict(item)
    detail, content = item.get('detail', {}), item.get('content', {})
    source = detail.get('source', {})
    # Legacy normalized metadata is evidence; never decode arbitrary source-key syntax.
    platform = source.get('platform') or {'onebot':'qq','feishu-v2':'feishu'}.get(detail.get('protocol'), 'unknown')
    region = source.get('region_id', detail.get('peer_id', ''))
    if not region and platform == 'feishu':
        # Finite compatibility for the known, old Feishu chat envelope only.
        prefix = 'feishu:' + str(detail.get('tenant_key','')) + ':chat:'
        key = item.get('source_key','')
        if key.startswith(prefix):
            region = key[len(prefix):]
    return {k:v for k,v in {
        'platform':platform, 'kind':source.get('kind', content.get('message_type', 'unknown') if platform=='qq' else 'unknown'),
        'region_id':region, 'temp_group_id':detail.get('temp_group_id'),
        'account_id':source.get('account_id', detail.get('self_id')),
        'tenant_id':source.get('tenant_id', detail.get('tenant_key')),
        'name':source.get('display_name')}.items() if v is not None}


def project_observation(item, *, ref: str, role: str):
    item = _dict(item)
    content, detail = item.get('content', {}), item.get('detail', {})
    result = {'ref':ref, 'role':role, 'text':content.get('text', ''),
        'sender':{'id':content.get('sender_id', ''), 'name':content.get('sender_name', '')},
        'observed_at':item.get('observed_at')}
    for key in ('sent_at','reply_to','root_id','parent_id','thread_id'):
        if key in detail:
            result[key] = detail[key]
    if content.get('media'):
        result['media'] = content['media']
    mentioned = [s.get('data',{}).get('qq') for s in content.get('segments',[]) if s.get('type')=='at']
    if mentioned:
        result['mentioned_ids'] = mentioned
    # Real platform IDs can be needed for replying; internal observation UUIDs cannot.
    if item.get('message_id'):
        result['platform_message_id'] = item['message_id']
    return result


def reply_target(item, *, source_ref: str):
    """Associate this message with its real reply region, not its quoted message/sender."""
    item = _dict(item)
    source = source_view(item)
    target = {'source': source_ref, 'platform': source['platform'], 'kind': source['kind']}
    for key in ('account_id', 'tenant_id', 'temp_group_id'):
        if source.get(key):
            target[key] = source[key]
    region = source.get('region_id')
    if region:
        if source['platform'] == 'qq' and source['kind'] in {'private', 'group'}:
            target['user_id' if source['kind'] == 'private' else 'group_id'] = region
        elif source['platform'] == 'feishu':
            target['chat_id'] = region
    if item.get('message_id'):
        target['platform_message_id'] = item['message_id']
    if item.get('detail', {}).get('thread_id'):
        target['thread_id'] = item['detail']['thread_id']
    return target


def render_round(wake, *, message_refs: dict[str,str], source_refs: dict[str,str]) -> str:
    wake = _dict(wake)
    sources, messages = {}, []
    work_ids = {item['id'] for item in wake['items']}
    current = wake.get('window_items') or wake['items']
    for item in current:
        source_ref = source_refs[item['source_key']]
        sources[source_ref] = source_view(item)
        messages.append(project_observation(item,ref=message_refs[item['id']],role='work' if item['id'] in work_ids else 'current') |
            {'source':source_ref, 'reply_target':reply_target(item, source_ref=source_ref)})
    history = []
    for item in wake.get('history_items',[]):
        source_ref = source_refs[item['source_key']]
        sources.setdefault(source_ref,source_view(item))
        history.append(project_observation(item,ref=message_refs[item['id']],role='context') | {'source':source_ref})
    snapshot = wake['channel_snapshot']
    context = {k:snapshot[k] for k in ('goal','progress') if snapshot.get(k)}
    return 'ARGUS CURRENT MESSAGES\n' + json.dumps({'context':context,'sources':sources,'messages':messages,
        'work_refs':[message_refs[item['id']] for item in wake['items']], 'history':history}, ensure_ascii=False, sort_keys=True)


def observations_visible(body: str, text: str) -> bool:
    if not body:
        return False
    if body in text:
        return True
    marker = '<plugin_context>'
    start = text.find(marker)
    while start >= 0:
        # Decode actual envelopes without splitting on marker-like text inside JSON.
        try:
            blocks, _ = json.JSONDecoder().raw_decode(text[start+len(marker):].lstrip())
        except (ValueError,TypeError):
            blocks = []
        if isinstance(blocks,list) and any(isinstance(block,dict) and
                isinstance(block.get('content'),str) and body in block['content'] for block in blocks):
            return True
        start = text.find(marker,start+len(marker))
    return False


def initial_task_prompt(wake) -> str:
    wake = _dict(wake)
    subject = semantic_subject(wake.get('window_items') or wake['items'],max_chars=256)
    return ('Consider the conversation observations supplied by the Argus plugin. Understand the complete '
        'current window before deciding whether to reply or act.\nSubject (observed data): ' + subject)


def semantic_subject(items, *, max_chars: int = 8192) -> str:
    lines = []
    for item in items:
        content = _dict(item).get('content',{})
        text = content.get('text') or content.get('media_summary') or '[non-text message]'
        name = content.get('sender_name') or content.get('sender_id')
        lines.append((str(name) + ': ' if name else '') + text)
    return '\n'.join(lines)[:max_chars]
