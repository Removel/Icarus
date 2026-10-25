"""Read-only SnowLuma OneBot v11 HTTP action client."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import httpx

from .models import HistoryMessage, SnowLumaError


class SnowLumaClient:
    """Bounded read-only access to SnowLuma's OneBot action endpoint.

    SnowLuma accepts ``POST {"action": ..., "params": ...}`` with a bearer token.
    This client deliberately exposes only the two history actions; it does not
    provide a general action proxy.
    """

    def __init__(self, base_url, token, *, self_id, timeout=5.0, count_limit=50, transport=None):
        if not base_url or not token or not self_id:
            raise ValueError('base_url, token and self_id are required')
        self._base_url = base_url.rstrip('/') + '/'
        self._token = token
        self._self_id = str(self_id)
        self._count_limit = count_limit
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)

    async def group_history(self, group_id, *, count=20, message_id=None, reverse_order=None):
        params = {'group_id': self._positive(group_id, 'group_id'), 'count': self._count(count)}
        self._anchor(params, message_id, reverse_order)
        return await self._history('get_group_msg_history', params)

    async def friend_history(self, user_id, *, count=20, message_id=None, reverse_order=None):
        params = {'user_id': self._positive(user_id, 'user_id'), 'count': self._count(count)}
        self._anchor(params, message_id, reverse_order)
        return await self._history('get_friend_msg_history', params)

    async def aclose(self):
        await self._client.aclose()

    @staticmethod
    def _positive(value, name):
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValueError(f'{name} must be a positive integer') from None
        if number <= 0:
            raise ValueError(f'{name} must be a positive integer')
        return number

    def _count(self, value):
        try:
            count = int(value)
        except (TypeError, ValueError):
            count = 20
        return max(1, min(count, self._count_limit))

    @staticmethod
    def _anchor(params, message_id, reverse_order):
        if message_id is not None:
            try:
                params['message_id'] = int(message_id)
            except (TypeError, ValueError):
                raise ValueError('message_id must be an integer') from None
        if reverse_order is not None:
            params['reverse_order'] = bool(reverse_order)

    async def _history(self, action, params):
        payload = await self._call(action, params)
        data = payload.get('data')
        messages = data.get('messages') if isinstance(data, dict) else None
        if not isinstance(messages, list):
            return ()
        parsed = (self._message(item) for item in messages)
        return tuple(value for value in parsed if value is not None)

    async def _call(self, action, params):
        try:
            response = await self._client.post(
                self._base_url, json={'action': action, 'params': params},
                headers={'Authorization': f'Bearer {self._token}'})
        except httpx.HTTPError as error:
            raise SnowLumaError(f'{action} transport failed') from error
        if response.status_code != 200:
            raise SnowLumaError(f'{action} HTTP {response.status_code}', status=response.status_code)
        try:
            payload = response.json()
        except ValueError:
            raise SnowLumaError(f'{action} returned non-JSON') from None
        if not isinstance(payload, dict):
            raise SnowLumaError(f'{action} returned an invalid envelope')
        if payload.get('status') != 'ok' or payload.get('retcode') != 0:
            raise SnowLumaError(f'{action} failed', status=payload.get('status'),
                                retcode=payload.get('retcode'), wording=payload.get('wording'))
        return payload

    def _message(self, item):
        if not isinstance(item, dict):
            return None
        try:
            message_id = str(item['message_id'])
            stamp = item['time']
            if not message_id or message_id == '0' or isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
                return None
            if not math.isfinite(stamp):
                return None
            sent_at = datetime.fromtimestamp(float(stamp), UTC)
            sender = item.get('sender')
            if not isinstance(sender, dict):
                return None
            sender_id = sender.get('user_id') or item.get('user_id')
            if sender_id is None:
                return None
            sender_id = str(sender_id)
            if not sender_id or sender_id == '0':
                return None
            message = item.get('message')
            segments = tuple(message) if isinstance(message, list) else ()
            raw_message = item.get('raw_message') if isinstance(item.get('raw_message'), str) else None
            name = sender.get('card') or sender.get('nickname')
            role = sender.get('role')
            return HistoryMessage(
                message_id=message_id,
                sent_at=sent_at,
                sender_id=sender_id,
                sender_name=name if isinstance(name, str) and name else None,
                sender_role=role if isinstance(role, str) and role else None,
                is_self=sender_id == self._self_id,
                message=segments,
                raw_message=raw_message,
                group_id=self._optional_id(item.get('group_id')),
                user_id=self._optional_id(item.get('user_id')),
                target_id=self._optional_id(item.get('target_id')),
            )
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            return None

    @staticmethod
    def _optional_id(value):
        if value is None:
            return None
        text = str(value)
        return text if text and text != '0' else None
