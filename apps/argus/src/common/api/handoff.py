"""Thin authenticated routes; handoff state remains in the service/repository."""

import hmac
import json
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from ..repositories.handoff_repository import HandoffConflict
from ..repositories.observation_repository import NotFound
from ..schemas.handoff import BindBody, BindingBody, SessionPendingBody, DeliverBody, ReadBody, HistoryBody, WaitBody, ReportBody, FinalizeBody


MODELS = {'bind': BindBody, 'deliver': DeliverBody, 'read': ReadBody, 'history': HistoryBody,
          'wait': WaitBody, 'report': ReportBody, 'finalize': FinalizeBody, 'available': BindingBody}


def create_handoff_router(service, *, token):
    router = APIRouter(prefix='/api/argus/internal')

    @router.post('/sessions/{session_id}/pending')
    async def pending(session_id: UUID, request: Request):
        supplied = request.headers.get('authorization', '')
        if not token or not hmac.compare_digest(supplied.encode(), f'Bearer {token}'.encode()):
            return error(401, 'unauthorized', version=2)
        try:
            raw = await limited_body(request)
            body = SessionPendingBody.model_validate(json.loads(raw))
            result = await service.session_pending(str(session_id), body.scope.model_dump())
            return JSONResponse(result)
        except (ValueError, ValidationError):
            return error(422, 'invalid_params', version=2)
        except HandoffConflict as exception:
            return error(409, exception.code, version=2)
        except SQLAlchemyError:
            return error(503, 'unavailable', retryable=True, version=2)

    @router.post('/wakes/{wake_id}/{operation}')
    async def dispatch(wake_id: UUID, operation: str, request: Request):
        supplied = request.headers.get('authorization', '')
        if not token or not hmac.compare_digest(supplied.encode(), f'Bearer {token}'.encode()):
            return error(401, 'unauthorized')
        if operation not in MODELS:
            return error(404, 'not_found')
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 1048576:
                return error(413, 'payload_too_large')
            chunks.append(chunk)
        try:
            body = MODELS[operation].model_validate(json.loads(b''.join(chunks)))
        except (ValueError, ValidationError):
            return error(422, 'invalid_params')
        payload = body.model_dump(exclude={'protocol_version', 'binding_id'}, exclude_none=True)
        try:
            if operation == 'bind':
                result = await service.bind(wake_id, {**payload, 'protocol_version': body.protocol_version})
            else:
                binding = await service.repository.binding(body.binding_id)
                if binding.get('protocol_version', 1) != body.protocol_version:
                    return error(409, 'protocol_mismatch', version=body.protocol_version)
                if binding['wake_id'] != str(wake_id):
                    return error(403, 'scope_mismatch', version=body.protocol_version)
                if operation == 'available':
                    result = await service.available(body.binding_id)
                elif operation == 'read':
                    result = await service.read(body.binding_id, **payload)
                elif operation == 'deliver':
                    result = await service.deliver(body.binding_id, payload['observation_ids'])
                elif operation in {'history', 'wait'}:
                    result = await getattr(service, operation)(body.binding_id, **payload)
                elif operation == 'report':
                    result = await service.report(body.binding_id, payload)
                else:
                    result = await service.finalize(body.binding_id, payload)
            encoded = json.dumps(result, ensure_ascii=False, default=str).encode()
            if len(encoded) > 1048576:
                return error(503, 'response_too_large', retryable=True, version=body.protocol_version)
            status = 409 if result.get('disposition') == 'rejected' else 200
            return JSONResponse(result, status_code=status)
        except NotFound:
            return error(404, 'not_found', version=body.protocol_version)
        except HandoffConflict as exception:
            code = exception.code
            status = 403 if code in {'scope_mismatch', 'content_restricted'} else 422 if code.startswith('invalid_') else 409
            return error(status, code, version=body.protocol_version)
        except SQLAlchemyError:
            return error(503, 'unavailable', retryable=True, version=body.protocol_version)

    return router


async def limited_body(request):
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > 1048576:
            raise ValueError('payload_too_large')
        chunks.append(chunk)
    return b''.join(chunks)


def error(status, code, *, retryable=False, version=1):
    return JSONResponse({'protocol_version': version, 'code': code, 'retryable': retryable}, status_code=status)
