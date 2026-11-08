"""Supplemental batches: stable claims and final-tool-view delivery evidence."""
import asyncio
import json
from uuid import uuid4

from icarus.core.tools import ToolExecutionResult
from .presentation import render_round


def work_items(binding):
    return binding['wake']['items'] + [item for batch in binding.get('inbox_batches',[]) for item in batch['items']]


def visible_output(output, messages):
    for message in messages:
        if message.role != 'tool':
            continue
        content=message.content
        text=content if isinstance(content,str) else ''.join(getattr(p,'text','') for p in (content or ()))
        try:
            value=json.loads(text)
        except (ValueError,TypeError):
            continue
        if (isinstance(value,dict) and value.get('success') is True and value.get('output')==output
                and not value.get('metadata',{}).get('result_truncated',False)):
            return True
    return False


def batch_page(batch, *, max_chars=8192):
    text=json.dumps(batch['view'],ensure_ascii=False,sort_keys=True)
    offset=batch.get('read_offset',0)
    if offset==0 and len(text)<=max_chars:
        output=batch['view']
        end=len(text)
    else:
        end=min(len(text),offset+max_chars)
        output={'observation_text':text[offset:end],'text_offset':offset,'next_text_offset':end,
            'complete':end==len(text),'work_refs':batch['view']['work_refs'],
            'read_help':'Call argus_receive again to continue. Reduce max_chars if this page is truncated; offsets are managed by code.'}
    issued=batch.setdefault('issued_pages',[])
    if not any(page['start']==offset and page['end']==end for page in issued):
        issued.append({'start':offset,'end':end,'output':output})
    return output


async def synchronize(tool, client, task_id, binding, task_messages):
    for batch in binding.get('inbox_batches',[]):
        if batch.get('delivered'):
            continue
        length=len(json.dumps(batch['view'],ensure_ascii=False,sort_keys=True))
        if visible_output(batch['view'],task_messages):
            batch['read_offset']=length
        else:
            while True:
                page=next((page for page in batch.get('issued_pages',[]) if
                    page['start']==batch.get('read_offset',0) and visible_output(page['output'],task_messages)),None)
                if page is None:
                    break
                batch['read_offset']=page['end']
                batch['issued_pages']=[p for p in batch['issued_pages'] if p['start']>=page['end']]
        if batch.get('read_offset',0)==length:
            receipt=await tool._callback(client,task_id,binding,'agent.delivered',
                {'observation_ids':[item['id'] for item in batch['items']]},'delivered-batch-'+batch['batch_id'])
            batch['delivered']=receipt.get('applied') is True


async def available(client, binding):
    response=await client.get(binding['wake']['argus_url']+'/agent/inbox/'+binding['wake']['binding_id'])
    response.raise_for_status()
    value=response.json()
    if type(value.get('pending_count')) is not int or value['pending_count']<0:
        raise ValueError('Invalid inbox state')
    return value


async def receive(tool, client, task_id, binding, payload, task_messages):
    channel=tool.plugin.task_channels.get(task_id)
    await synchronize(tool,client,task_id,binding,task_messages)
    pending=next((batch for batch in binding.get('inbox_batches',[]) if not batch.get('delivered')),None)
    if pending:
        return ToolExecutionResult(True,output=batch_page(pending,max_chars=payload.get('max_chars',8192)))
    timeout=payload.get('timeout_seconds',0)
    deadline=asyncio.get_running_loop().time()+timeout
    while not binding.get('receive_request'):
        channel.raise_if_cancelled()
        signal=binding.get('availability_signal')
        if signal is not None:
            signal.clear()
        state=await available(client,binding)
        if state['pending_count'] or asyncio.get_running_loop().time()>=deadline:
            break
        remaining=max(0,deadline-asyncio.get_running_loop().time())
        if signal is None:
            await asyncio.sleep(min(1.0,remaining))
        else:
            try:
                await asyncio.wait_for(signal.wait(),timeout=remaining)
            except TimeoutError:
                pass
    channel.raise_if_cancelled()
    if not binding.get('receive_request'):
        if not state['pending_count']:
            return ToolExecutionResult(True,output={'messages':[],'work_refs':[binding['message_refs'][item['id']] for item in work_items(binding)]})
        binding['receive_request']={'request_id':'receive-'+uuid4().hex,'payload':{'limit':payload.get('limit',20)}}
    request=binding['receive_request']
    result=await tool._callback(client,task_id,binding,'agent.batch_requested',request['payload'],request['request_id'])
    if result.get('applied') is not True:
        binding.pop('receive_request',None)
        return ToolExecutionResult(False,output=tool._receipt_view(result),error='Argus inbox rejected claim')
    items=result['items'];body=result.get('window_items') or items
    bridge=tool.plugin.bridge;identity=bridge.identity_for_wake(binding['wake'])
    messages,sources=await bridge.references.ensure(identity,observation_ids=tuple(item['id'] for item in body),
        source_keys=tuple(dict.fromkeys(item['source_key'] for item in body)))
    binding['message_refs'],binding['source_refs']=messages,sources
    view=json.loads(render_round({'items':items,'window_items':body,'channel_snapshot':{}},message_refs=messages,source_refs=sources).split('\n',1)[1]) if items else {'messages':[]}
    existing=work_items(binding)
    view['work_refs']=[messages[item['id']] for item in existing+items]
    if items:
        batch={'batch_id':result['batch_id'],'items':items,'view':view,'delivered':False}
        binding.setdefault('inbox_batches',[]).append(batch)
        output=batch_page(batch,max_chars=payload.get('max_chars',8192))
    else:
        output=view
    binding.pop('receive_request',None)
    return ToolExecutionResult(True,output=output)
