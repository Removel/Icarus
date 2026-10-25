"""Evidence facts, not inferred QQ success or a server adapter.

The optional ``structured_content.business_receipt`` v1 shape is an explicit
integration contract. No bundled MCP server is claimed to emit it. In its absence
an MCP execution remains unconfirmed even when its transport reports success.
"""

from datetime import UTC, datetime


def business_receipt(output):
    if not isinstance(output, dict):
        return None
    structured = output.get('structured_content')
    if not isinstance(structured, dict):
        return None
    receipt = structured.get('business_receipt')
    if (not isinstance(receipt, dict) or receipt.get('kind') != 'business_action_receipt'
            or type(receipt.get('version')) is not int or receipt['version'] != 1
            or type(receipt.get('success')) is not bool):
        return None
    target, message_id = receipt.get('target'), receipt.get('message_id')
    if (not isinstance(target, dict) or not target or len(target) > 8
            or any(not isinstance(key, str) or not isinstance(value, str) or not 0 < len(value) <= 256
                   for key, value in target.items())
            or not isinstance(message_id, str) or not 0 < len(message_id) <= 256):
        return None
    return {'kind': receipt['kind'], 'version': 1, 'success': receipt['success'],
            'target': dict(target), 'message_id': message_id}


class ActionFacts:
    def __init__(self, plugin):
        self.plugin = plugin
        self.seen = set()

    def restore(self, rows):
        for row in rows:
            if row.update_type == 'argus_action':
                self.seen.add((row.task_id, row.payload.get('tool_call_id')))

    async def completed(self, event):
        key = (event.task_id, event.tool_call.id)
        if key in self.seen or self.plugin.append_record is None:
            return
        receipt = business_receipt(event.result.output)
        # Only the existing MCP execute envelope or an explicit receipt represents
        # an external-action attempt; don't fabricate actions from arbitrary tools.
        if event.tool_call.name != 'mcp_tool_execute' and receipt is None:
            return
        if not event.result.success:
            status = 'failed' if receipt is not None and not receipt['success'] else 'unconfirmed'
        else:
            status = 'unconfirmed' if receipt is None else 'confirmed' if receipt['success'] else 'failed'
        arguments = event.tool_call.arguments
        await self.plugin._append(task_id=event.task_id, update_type='argus_action', payload={
            'session_id': self.plugin.session_id or self.plugin.context.metadata.get('session_id'),
            'tool_call_id': event.tool_call.id, 'tool_name': event.tool_call.name,
            'tool_ref': arguments.get('tool_ref') if isinstance(arguments, dict) else None,
            'occurred_at': event.occurred_at.isoformat() if event.occurred_at else datetime.now(UTC).isoformat(),
            'target': receipt['target'] if receipt else None,
            'transport_success': event.result.success, 'business_status': status, 'receipt': receipt})
        self.seen.add(key)
