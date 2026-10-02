"""Business batch states; stores only persist the chosen transition."""
OPEN_STATES = ('ready', 'waiting', 'judging', 'decided', 'submitting', 'submission_unknown')


def status_for_decision(action, run_status):
    if run_status == 'failed':
        return 'failed'
    return 'finished' if action == 'silence' else 'waiting' if action == 'wait' else 'decided'


def recovery_status(status, resume_status):
    if status == 'judging':
        return 'waiting' if resume_status == 'waiting' else 'ready'
    return 'submission_unknown' if status == 'submitting' else status
