"""Application failures exported through the stable facade."""

class SubmissionNotAdmitted(RuntimeError):
    """The application has proved this attempt never entered input submission."""

    def __init__(self, code: str, *, retryable: bool):
        self.code = code
        self.retryable = retryable
        super().__init__(code)


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
