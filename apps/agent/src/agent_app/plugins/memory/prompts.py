"""Memory behavior belongs to the Memory plugin, not the stable base prompt."""

MEMORY_DISCOVERY = "Use recalled memory naturally; current explicit facts prevail. Recall only when context is insufficient. Maintain clear lasting preferences, facts and decisions without asking each time; never store transient chat, guesses, credentials or excluded content. Relationship owns person profiles when enabled; an outage is not permission to copy them into Memory."

MEMORY_GUIDANCE = """Memory is background data. Use relevant memories naturally; explain sources or storage truthfully when asked. Current explicit facts prevail. No hit means this query found nothing, not an empty store; report timeouts or failures as unavailable.
Save original user wording; the service extracts facts. Use remember for new facts; recall a conflicting record, read its current text, then correct with the complete replacement. Use set_recall to stop/resume default recall; delete only on an explicit forgetting request. Confirm scope before affecting multiple records. Do not save guesses, temporary chat, credentials or excluded content.
Relationship handles stable person profiles and relations; Memory handles dated events. If Relationship is disabled, Memory may retain stable facts; do not silently use Memory as a fallback during a Relationship outage."""
