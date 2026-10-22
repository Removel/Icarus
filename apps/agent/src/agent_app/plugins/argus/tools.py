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
        if self.operation == 'read':
            properties = {'observation_id': {'type': 'string', 'format': 'uuid'},
                          'text_offset': {'type': 'integer', 'minimum': 0},
                          'max_chars': {'type': 'integer', 'minimum': 1, 'maximum': 20000}}
            required = ['observation_id']
        elif self.operation in {'history', 'wait'}:
            properties = {'cursor': {'type': 'string', 'maxLength': 2048},
                          'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50}}
            if self.operation == 'wait':
                properties['timeout_seconds'] = {'type': 'number', 'minimum': 0, 'maximum': 30}
        else:
            properties = {'items': {'type': 'array', 'minItems': 1, 'maxItems': 50, 'items': {
                'type': 'object', 'properties': {'observation_id': {'type': 'string'},
                    'status': {'enum': ['handled', 'no_action']}, 'reason': {'type': 'string', 'maxLength': 1000}},
                'required': ['observation_id', 'status'], 'additionalProperties': False}}}
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
