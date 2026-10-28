import secrets

from fastapi import Header, Request

from errors import ServiceError


async def authenticate(request: Request, authorization: str | None = Header(default=None)):
    expected = "Bearer " + request.app.state.settings.api_token.get_secret_value()
    if authorization is None or not secrets.compare_digest(authorization.encode(), expected.encode()):
        raise ServiceError("UNAUTHORIZED", "valid Bearer token required", 401)


def persons(request: Request):
    return request.app.state.persons
