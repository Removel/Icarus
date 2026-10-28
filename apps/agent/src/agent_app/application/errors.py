"""Application failures exported through the stable facade."""

class SubmissionConflictError(ValueError):
    pass

class AgentRuntimeStoppingError(RuntimeError):
    pass

class SessionNotFoundError(KeyError):
    pass

class SessionAlreadyExistsError(ValueError):
    pass

class ConversationHistoryCorruptError(RuntimeError):
    pass
