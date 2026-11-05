"""App-owned source fact projection, including protocol-owned legacy decoding."""
from argus_app.contracts.source import SourceDescriptor
from .qq_protocol.plugin import legacy_source as qq_legacy
from .feishu_protocol.plugin import legacy_source as feishu_legacy


def source_descriptor(observation):
    supplied = observation.detail.get('source')
    if supplied is not None:
        return SourceDescriptor.model_validate(supplied).model_dump(exclude_none=True)
    for decode in (qq_legacy, feishu_legacy):
        found = decode(observation.source_key, observation.content, observation.detail)
        if found is not None:
            return found
    return {'platform': 'unknown', 'kind': 'unknown', 'region_id': ''}
