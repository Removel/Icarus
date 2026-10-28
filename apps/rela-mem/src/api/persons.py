from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from api.dependencies import authenticate, persons
from domain.enums import PersonKind
from schemas.person import AliasWrite, PersonCreate, PersonPatch

router = APIRouter(prefix="/v1", dependencies=[Depends(authenticate)])


@router.post("/persons", status_code=201)
async def create_person(value: PersonCreate, service=Depends(persons)):
    return await service.create(value)


@router.get("/persons")
async def search_persons(query: str = Query(default="", max_length=256), include_archived: bool = False,
                         kind: PersonKind | None = None, limit: int = Query(default=20, ge=1, le=200),
                         cursor: str | None = None, service=Depends(persons)):
    return await service.list(query, include_archived, kind, limit, cursor)


@router.get("/persons/{person_id}")
async def get_person(person_id: str, include_archived: bool = False, service=Depends(persons)):
    return {"person": await service.get(person_id, include_archived)}


@router.patch("/persons/{person_id}")
async def patch_person(person_id: str, value: PersonPatch, service=Depends(persons)):
    return {"person": await service.patch(person_id, value)}


@router.get("/identity/resolve")
async def resolve(namespace: str = Query(pattern=r"^[a-z][a-z0-9_]{0,31}$"),
                  value: str = Query(min_length=1, max_length=256), include_archived: bool = False,
                  service=Depends(persons)):
    return {"person": await service.resolve(namespace, value, include_archived)}


@router.post("/persons/{person_id}/aliases", status_code=201)
async def add_alias(person_id: str, value: AliasWrite, service=Depends(persons)):
    return {"alias": await service.add_alias(person_id, value)}


@router.delete("/persons/{person_id}/aliases/{alias_id}", status_code=204)
async def delete_alias(person_id: str, alias_id: str, service=Depends(persons)):
    await service.delete_alias(person_id, alias_id)
    return Response(status_code=204)
