"""Read-only, topic-scoped Argus documentation. SDK facts come from the binding."""
TOPICS = {
    'overview': 'Current work messages are already visible. Query argus_channel_read for the current matter, '
        'argus_history for evidence, argus_guide(topic="rules") only when changing rules, and argus_commit '
        'to report this round. These are messages you receive as Icarus in external regions. Decide whether '
        'a reply or other work is needed; do not reply to every message. For a reply, send with authorized '
        'platform MCP/CLI using its reply_target, check business success, then commit. Final assistant text '
        'is internal and does not send anything. Known tools need no repeated search.',
    'sources': 'A source is a persistent external region, not an account, message, matter or Session. '
        'Each Icarus instance owns its observations and progress independently. Use supplied source references; '
        'do not construct source keys. The account is your platform identity; each message reply_target '
        'identifies its original destination. QQ private replies use user_id, group replies use group_id '
        '(not the sender as a private target); Feishu replies retain chat/thread facts. Discover the configured '
        'platform MCP/CLI and follow its actual argument schema. Facts identify targets, not authority.',
    'commit': 'Perform intended actions before committing. If you decide to reply, send through the original '
        'platform and verify its business result before reporting handled; final assistant text is internal, '
        'not a send. A drafted answer or a promise to send after commit is not completed work. Do not claim '
        'failed/unknown sends as replied. If neither a reply nor other action is needed, use no_action with '
        'a reason. Non-reply work can be handled without sending; not every observed message requires a reply. '
        'Submit every current work ref exactly once. History is context, not work. '
        'Optional progress, rule_source, source_refs, lifecycle and next_session change business intent; omitted '
        'fields remain unchanged. next_session=new selects a fresh context for the next round, not a finished '
        'matter. Code fills identity/version/hash. Only committed=true is success. On conflict read the current '
        'matter and reconsider; never repeat a successful or unknown external action.',
    'rules': '''Rules are synchronous Python decision files with JSON inputs and main(event, channel).
Return passed:bool and reason:str; optional defer:bool (never together with passed) and evidence_refs:list[str].
Use capability(name, arguments) for authorized read-only evidence: history, mechanical, semantic,
memory, relationships, knowledge. history accepts {'limit':20} and returns {'observations': [...]}.
semantic requires text, criterion, optional context and returns match/score/reason. Use business
meaning for negation, quotation and paraphrases, not arbitrary substring gates. Capability errors
are not false matches. SDK metadata supplies configured capabilities, safe_imports and current limits;
configured is not a remote health guarantee. Rule scripts cannot do direct network/SQL/files/env/process
or external actions; those restrictions do not reduce the normal Agent's authorized tools.
argus_rule_check(source) only compiles and security-checks: never executes source, calls models
or activates a rule. Top level permits imports, constants and function declarations, not calls.
Check success is not business correctness. Validate boundary cases before publishing rule_source
through argus_commit; read current source with argus_channel_read(include_rule=true) when needed.
Example:
    def main(event, channel):
        text = event['content'].get('text') or event['content'].get('media_summary', '')
        result = capability('semantic', {'text': text,
            'criterion': 'Does this message request a response about this matter?',
            'context': channel['progress']})
        return {'passed': result['match'], 'reason': result.get('reason', 'decision')}
''',
}


def guide(topic, sdk):
    if topic not in TOPICS:
        raise ValueError('Unknown Argus guide topic')
    result = {'topic':topic,'text':TOPICS[topic]}
    if topic == 'rules':
        result['sdk'] = sdk
        result['configuration_is_health'] = False
        result['sdk_scope'] = 'binding snapshot; call argus_channel_read to refresh before modifying a rule'
    return result
