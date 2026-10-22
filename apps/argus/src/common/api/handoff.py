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
from ..schemas.handoff import BindBody, DeliverBody, ReadBody, HistoryBody, WaitBody, ReportBody, FinalizeBody


MODELS = {'bind': BindBody, 'deliver': DeliverBody, 'read': ReadBody, 'history': HistoryBody,
          'wait': WaitBody, 'report': ReportBody, 'finalize': FinalizeBody}


def create_handoff_router(service, *, token):
    router = APIRouter(prefix='/api/argus/internal/wakes')

    @router.post('/{wake_id}/{operation}')
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
                result = await service.bind(wake_id, payload)
            else:
                binding = await service.repository.binding(body.binding_id)
                if binding['wake_id'] != str(wake_id):
                    return error(403, 'scope_mismatch')
                if operation == 'read':
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
                return error(503, 'response_too_large', retryable=True)
            status = 409 if result.get('disposition') == 'rejected' else 200
            return JSONResponse(result, status_code=status)
        except NotFound:
            return error(404, 'not_found')
        except HandoffConflict as exception:
            code = exception.code
            status = 403 if code in {'scope_mismatch', 'content_restricted'} else 422 if code.startswith('invalid_') else 409
            return error(status, code)
        except SQLAlchemyError:
            return error(503, 'unavailable', retryable=True)

    return router


def error(status, code, *, retryable=False):
    return JSONResponse({'protocol_version': 1, 'code': code, 'retryable': retryable}, status_code=status)
