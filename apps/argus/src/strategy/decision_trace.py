"""Diagnostic summaries never serialize arbitrary evidence or model text."""
from ..entity.decision import RouteDecision, InterventionResult, TaskRelationResult, TaskSnapshot
from ..entity.evidence import ConversationEvidence, RecentActivity

REASONS = frozenset({
    'not_required','self_only','explicit_address','semantic_context_required','jev','idle',
    'work_state_unresolved','queued_work','supplement','response_needed','busy',
    'task_context_missing','graph_failed','evidence_changed','target_changed','steer_rejected',
})


def safe_reason(value):
    return value if value in REASONS else 'other'


def safe_result(output):
    if isinstance(output, RouteDecision):
        return dict(action=output.action,reason=safe_reason(output.reason),target_task_id=output.target_task_id)
    if isinstance(output,(InterventionResult,TaskRelationResult)):
        return dict(verdict=output.verdict,reason=safe_reason(output.reason))
    if isinstance(output,TaskSnapshot):
        return dict(lifecycle=output.lifecycle,task_count=len(output.tasks),queued=output.queued_task_count,
                    stable=output.stable,history_complete=output.history_complete)
    if isinstance(output,ConversationEvidence):
        return dict(current_count=len(output.current),preceding_count=len(output.preceding),
                    following_count=len(output.following),truncated=output.truncated)
    if isinstance(output,RecentActivity):
        return dict(consecutive_no_action=output.consecutive_no_action,submissions_in_window=output.submissions_in_window)
    return output if isinstance(output,bool) else None


def safe_trace(run):
    nodes, edges = [], []
    for execution in run.executions.values():
        summary = dict(id=execution.id,status=execution.status,duration=execution.duration,
                       iteration=execution.iteration,error_type=execution.error_type)
        if execution.kind == 'edge':
            edges.append(summary)
        else:
            nodes.append(dict(summary,result=safe_result(execution.output)))
    return dict(run_id=run.run_id,status=run.status,nodes=nodes,edges=edges,
                selected_edges=list(run.selected_edges),error_type=run.error_type)
