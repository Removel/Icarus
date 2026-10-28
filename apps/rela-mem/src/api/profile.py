from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response

from api.dependencies import authenticate
from domain.enums import Source
from schemas.common import AttrKey
from schemas.profile import ProfileWrite

router = APIRouter(prefix="/v1/persons/{person_id}/profile", dependencies=[Depends(authenticate)])


def profiles(request: Request):
    return request.app.state.profiles


@router.get("")
async def current(person_id: str, q: str = Query(default="", max_length=256),
                  keys: list[str] = Query(default=[]), limit: int = Query(default=20, ge=1, le=200),
                  cursor: str | None = None, include_archived: bool = False, service=Depends(profiles)):
    return await service.current(person_id, q, keys, limit, cursor, include_archived)


@router.get("/keys")
async def current_keys(person_id: str, limit: int = Query(default=20, ge=1, le=200),
                       cursor: str | None = None, include_archived: bool = False, service=Depends(profiles)):
    result = await service.current(person_id, limit=limit, cursor=cursor, include_archived=include_archived)
    result["items"] = [item["attr_key"] for item in result["items"]]
    return result


@router.put("/{attr_key}")
async def write(person_id: str, attr_key: AttrKey, value: ProfileWrite, service=Depends(profiles)):
    return {"entry": await service.write(person_id, attr_key, value)}


@router.delete("/{attr_key}", status_code=204)
async def delete(person_id: str, attr_key: AttrKey, source: Source,
                 source_ref: str | None = Query(default=None, max_length=256, min_length=1), service=Depends(profiles)):
    await service.delete(person_id, attr_key, source, source_ref)
    return Response(status_code=204)


@router.get("/{attr_key}/history")
async def history(person_id: str, attr_key: AttrKey, limit: int = Query(default=20, ge=1, le=200),
                  cursor: str | None = None, include_archived: bool = False, service=Depends(profiles)):
    return await service.history(person_id, attr_key, limit, cursor, include_archived)
