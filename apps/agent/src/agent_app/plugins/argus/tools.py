"""Four fixed-scope tools share one async backend and the host's sync bridge."""

from jsonschema import validate, ValidationError

from icarus.core.models.messages import ToolDefinition
from icarus.core.tools import BaseTool, ToolExecutionResult


class ArgusTool(BaseTool):
    def __init__(self, plugin, operation):
        self.plugin, self.operation = plugin, operation

    @property
    def definition(self):
        properties, required = {}, []
        identity = 'observation_id' if getattr(self.plugin, 'protocol_version', 1) == 1 else 'ref'
        reference = {'type': 'string', 'format': 'uuid'} if identity == 'observation_id' else {
            'type': 'string', 'pattern': '^#[1-9][0-9]*$'}
        if self.operation == 'read':
            properties = {identity: reference,
                          'text_offset': {'type': 'integer', 'minimum': 0},
                          'max_chars': {'type': 'integer', 'minimum': 1, 'maximum': 20000}}
            required = [identity]
        elif self.operation in {'history', 'wait'}:
            properties = {'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50}}
            if identity == 'observation_id':
                properties['cursor'] = {'type': 'string', 'maxLength': 2048}
            if self.operation == 'wait':
                properties['timeout_seconds'] = {'type': 'number', 'minimum': 0, 'maximum': 30}
        else:
            properties = {'items': {'type': 'array', 'minItems': 1, 'maxItems': 50, 'items': {
                'type': 'object', 'properties': {identity: reference,
                    'status': {'enum': ['handled', 'no_action']}, 'reason': {'type': 'string', 'maxLength': 1000}},
                'required': [identity, 'status'], 'additionalProperties': False}}}
            required = ['items']
        return ToolDefinition(f'argus_{self.operation}', f'{self.operation.title()} observations within the bound chat only.',
                              {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False})

    def invoke(self, arguments, *, task_id=None, tool_call_id=None, **execution):
        runner = self.plugin._loop_runner
        if runner is None:
            return ToolExecutionResult(False, error='runtime_loop_unavailable')
        return runner.run(lambda: self.ainvoke(arguments, task_id=task_id, tool_call_id=tool_call_id))

    async def ainvoke(self, arguments, *, task_id=None, tool_call_id=None, **execution):
        try:
            validate(arguments, self.definition.input_schema)
        except ValidationError:
            return ToolExecutionResult(False, error='invalid_params')
        return await self.plugin.execute(self.operation, arguments, task_id=task_id, tool_call_id=tool_call_id)
