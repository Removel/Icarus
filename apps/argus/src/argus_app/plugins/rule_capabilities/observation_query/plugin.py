from icarus.core.plugin import BasePlugin, handles
from argus_app.contracts.capabilities import ObservationQuery, ObservationQueryRequest
from argus_app.plugins.rule_capabilities.responses import ok, failure


class ObservationQueryPlugin(BasePlugin):
    provided_capabilities = (ObservationQuery,)

    def __init__(self, plugin_id, store):
        super().__init__(plugin_id)
        self.store = store

    @handles(ObservationQueryRequest)
    async def query(self, source, request):
        try:
            if source != 'workflow-rules':
                return failure(request, 'query_forbidden')
            if request.channel_id:
                channel = await self.store.channel(request.channel_id)
                allowed_sources = set(channel.subscriptions)
            else:
                # Only the trusted workflow requester may query before a Channel
                # exists; the persisted trigger grants exactly its own source.
                trigger = await self.store.observation(request.args.get('discovery_observation_id', ''))
                if trigger.kind != 'message.observed':
                    return failure(request, 'source_forbidden')
                allowed_sources = {trigger.source_key}
            sources = request.args.get('source_keys', list(allowed_sources))
            if not isinstance(sources, list) or not sources or not set(sources) <= allowed_sources:
                return failure(request, 'source_forbidden')
            limit = request.args.get('limit', 20)
            if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
                return failure(request, 'query_invalid', 'limit must be 1..100')
            # Only WorkflowRules can submit queries; bounds are captured by its broker.
            if source != 'workflow-rules':
                return failure(request, 'query_forbidden')
            rows = await self.store.observations(source_keys=sources, limit=limit,
                before=request.args.get('before'), text=request.args.get('text'),
                bounds=request.args.get('source_bounds'))
            return ok(request, {'observations': [row.model_dump(mode='json') for row in rows]})
        except Exception as exc:
            return failure(request, getattr(exc, 'code', 'query_failed'), str(exc))
