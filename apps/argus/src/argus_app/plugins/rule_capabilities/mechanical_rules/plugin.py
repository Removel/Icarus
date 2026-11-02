from icarus.core.plugin import BasePlugin, handles
from argus_app.contracts.capabilities import MechanicalMatch, MechanicalMatchRequest
from argus_app.plugins.rule_capabilities.responses import ok, failure


class MechanicalRulesPlugin(BasePlugin):
    provided_capabilities = (MechanicalMatch,)

    @handles(MechanicalMatchRequest)
    async def match(self, source, request):
        text, needles = request.args.get('text'), request.args.get('contains')
        if not isinstance(text, str) or not isinstance(needles, list) or len(needles) > 100 or any(not isinstance(n, str) for n in needles):
            return failure(request, 'mechanical_invalid')
        return ok(request, {'match': all(needle in text for needle in needles)})
