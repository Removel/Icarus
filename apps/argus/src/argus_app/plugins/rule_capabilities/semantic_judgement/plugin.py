from icarus.core.plugin import BasePlugin, handles
from argus_app.contracts.capabilities import SemanticJudge, SemanticJudgeRequest
from argus_app.plugins.rule_capabilities.responses import ok, failure


class SemanticJudgementPlugin(BasePlugin):
    provided_capabilities = (SemanticJudge,)

    def __init__(self, plugin_id, semantic=None):
        super().__init__(plugin_id)
        self.semantic = semantic

    @handles(SemanticJudgeRequest)
    async def judge(self, source, request):
        if self.semantic is None:
            return failure(request, 'semantic_unavailable', unavailable=True)
        try:
            result = await self.semantic(request.args)
            if not isinstance(result, dict) or type(result.get('match')) is not bool:
                return failure(request, 'semantic_invalid', 'callback must return typed match bool')
            return ok(request, result)
        except Exception as exc:
            return failure(request, 'semantic_unavailable', str(exc), unavailable=True)
