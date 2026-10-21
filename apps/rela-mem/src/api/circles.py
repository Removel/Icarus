from fastapi import APIRouter, Depends, Query, Request, Response

from api.dependencies import authenticate
from schemas.circle import CircleCreate, CirclePatch
from schemas.member import MemberWrite

router = APIRouter(prefix="/v1", dependencies=[Depends(authenticate)])


def circles(request: Request):
    return request.app.state.circles


def members(request: Request):
    return request.app.state.members


@router.post("/circles", status_code=201)
async def create(value: CircleCreate, service=Depends(circles)):
    return {"circle": await service.create(value)}


@router.get("/circles")
async def list_circles(query: str = Query(default="", max_length=256), kind: str | None = None,
                       include_archived: bool = False, limit: int = Query(default=20, ge=1, le=200),
                       cursor: str | None = None, service=Depends(circles)):
    return await service.list(query, kind, include_archived, limit, cursor)


@router.patch("/circles/{circle_id}")
async def patch(circle_id: str, value: CirclePatch, service=Depends(circles)):
    return {"circle": await service.patch(circle_id, value)}


@router.put("/circles/{circle_id}/members/{person_id}")
async def upsert_member(circle_id: str, person_id: str, value: MemberWrite, service=Depends(members)):
    return {"membership": await service.upsert(circle_id, person_id, value)}


@router.delete("/circles/{circle_id}/members/{person_id}", status_code=204)
async def leave_member(circle_id: str, person_id: str, service=Depends(members)):
    await service.leave(circle_id, person_id)
    return Response(status_code=204)


@router.get("/circles/{circle_id}/members")
async def list_members(circle_id: str, include_left: bool = False, include_archived: bool = False,
                       limit: int = Query(default=20, ge=1, le=200), cursor: str | None = None,
                       service=Depends(members)):
    return await service.list_members(circle_id, include_left, include_archived, limit, cursor)


@router.get("/persons/{person_id}/circles")
async def persons_circles(person_id: str, include_left: bool = False, include_archived: bool = False,
                          limit: int = Query(default=20, ge=1, le=200), cursor: str | None = None,
                          service=Depends(members)):
    return await service.list_circles(person_id, include_left, include_archived, limit, cursor)
