"""Capability response helpers; ordinary components, never registered plugins."""
from icarus.core.capabilities import CapabilityStatus
from argus_app.contracts.capabilities import RuleResponse


def ok(request, data):
    return [RuleResponse(correlation_id=request.correlation_id, task_id=request.task_id, status=CapabilityStatus.OK, data=data)]


def failure(request, code, message='', unavailable=False):
    return [RuleResponse(correlation_id=request.correlation_id, task_id=request.task_id,
        status=CapabilityStatus.UNAVAILABLE if unavailable else CapabilityStatus.FAILED,
        error_code=code, error_message=(message or code)[:512])]
