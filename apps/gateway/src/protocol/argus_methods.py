"""Authenticated narrow forwarding; durable execution belongs to Agent application."""

import hmac
import logging
import os

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from agent_app.application.errors import AgentRuntimeStoppingError
from agent_app.application.models.scoped_execution import ScopedWakeRequest, ScopedExecutionError
from agent_app.argus_protocol import Scope
from .argus_models import WakeParams, StatusParams, CancelParams, ReleaseParams
from .errors import GatewayRpcError, INVALID_PARAMS, BUSINESS_ERROR, INTERNAL_ERROR, METHOD_NOT_FOUND


class ArgusMethods:
    max_request_bytes = 1048576

    def __init__(self, runtime, *, token_loader=lambda: os.getenv('ARGUS_GATEWAY_TOKEN')):
        self.runtime, self.token_loader = runtime, token_loader

    async def dispatch(self, method, params, subscriptions):
        models = {'argus.wake': WakeParams, 'argus.status': StatusParams,
                  'argus.cancel': CancelParams, 'argus.release': ReleaseParams}
        if method not in models:
            raise GatewayRpcError(METHOD_NOT_FOUND, 'Method not found')
        expected = self.token_loader()
        supplied = params.get('caller_token') if isinstance(params, dict) else None
        if not expected or not isinstance(supplied, str) or not hmac.compare_digest(supplied.encode(), expected.encode()):
            raise GatewayRpcError(BUSINESS_ERROR, 'Unauthorized', data={'code': 'unauthorized', 'retryable': False})
        try:
            value = models[method].model_validate(params)
            payload = value.model_dump(exclude={'caller_token'})
            if method == 'argus.wake':
                request = ScopedWakeRequest.model_validate(payload)
                result = await self.runtime.scoped_submit(request)
                return {'protocol_version': 1, 'accepted': True, 'execution': result.model_dump(mode='json')}
            if method == 'argus.status':
                scope = Scope.model_validate(value.scope) if value.scope is not None else None
                result = await self.runtime.scoped_status(wake_id=value.wake_id, scope=scope)
                return {'protocol_version': 1, 'found': result is not None,
                        'execution': result.model_dump(mode='json') if result is not None else None}
            if method == 'argus.cancel':
                result = await self.runtime.scoped_cancel(value.wake_id, value.reason)
                return {'protocol_version': 1, 'accepted': True, 'execution': result.model_dump(mode='json')}
            result = await self.runtime.scoped_release(value.wake_id)
            return {'protocol_version': 1, 'released': True, 'execution': result.model_dump(mode='json')}
        except (ValidationError, ValueError):
            raise GatewayRpcError(INVALID_PARAMS, 'Invalid params') from None
        except ScopedExecutionError as error:
            data = {'code': error.code, 'retryable': error.retryable}
            if error.owner is not None:
                data['owner'] = error.owner.model_dump(mode='json')
            raise GatewayRpcError(BUSINESS_ERROR, error.code, data=data) from None
        except (AgentRuntimeStoppingError, SQLAlchemyError):
            raise GatewayRpcError(BUSINESS_ERROR, 'Unavailable', data={'code': 'unavailable', 'retryable': True}) from None
        except Exception as error:
            logging.getLogger('icarus.gateway.argus').error('Argus method failed: method=%s error_type=%s', method, type(error).__name__)
            raise GatewayRpcError(INTERNAL_ERROR, 'Internal error') from None
