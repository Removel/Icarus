"""Read-only capability metadata; configuration is not a health claim."""
from .checks import POLICY_VERSION, SAFE_IMPORTS


def rule_guide(config=None):
    configured = {
        'history': True, 'mechanical': True,
        'semantic': bool(config and config.semantic_api_key.get_secret_value()),
        'memory': bool(config and config.memory_endpoint and config.memory_user_id and config.memory_agent_id and config.memory_workspace_key),
        'relationships': bool(config and config.relationship_endpoint),
        'knowledge': bool(config and config.knowledge_endpoint and config.knowledge_base),
    }
    args = {
        'history': {'source_keys': 'optional authorized source list', 'limit': '1..100; default20', 'text': 'optional text'},
        'mechanical': {'text': 'string', 'contains': 'list of required substrings'},
        'semantic': {'text': 'string', 'criterion': 'business question', 'context': 'optional JSON context'},
        'memory': {'query': 'string', 'limit': '1..50'},
        'relationships': {'query': 'string, max256'},
        'knowledge': {'query': 'string'},
    }
    return {'version': 2, 'policy_version': POLICY_VERSION, 'entry': 'main(event, channel)',
            'safe_imports': sorted(SAFE_IMPORTS), 'max_source_bytes': 32768,
            'max_calls': 3, 'max_semantic_calls': 1,
            'capabilities': {name: {'configured': value, 'arguments': args[name],
                                   'returns': 'observations' if name == 'history' else 'match/score/reason' if name == 'semantic' else 'JSON evidence'}
                             for name, value in configured.items()}}


def channel_snapshot(channel, files):
    return {'id': channel.id, 'title': channel.title, 'goal': channel.goal,
            'progress': channel.progress, 'lifecycle': channel.lifecycle, 'version': channel.version,
            'source_keys': list(channel.subscriptions),
            'rule_source': files.read(channel.rule_path, channel.rule_hash) if channel.rule_path else ''}
