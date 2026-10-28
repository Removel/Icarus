from fastapi import APIRouter, Depends, Query, Request, Response

from api.dependencies import authenticate
from domain.enums import RelationStatus
from schemas.relation import RelationPatch, RelationWrite

router = APIRouter(prefix="/v1", dependencies=[Depends(authenticate)])


def relations(request: Request):
    return request.app.state.relations


@router.post("/relations")
async def upsert(value: RelationWrite, service=Depends(relations)):
    return await service.upsert(value)


@router.get("/relations")
async def list_relations(person_id: str | None = None, other_person_id: str | None = None,
                         circle_id: str | None = None, kind: str | None = None,
                         status: RelationStatus = RelationStatus.ACTIVE, include_incoming: bool = True,
                         include_archived: bool = False, limit: int = Query(default=20, ge=1, le=200),
                         cursor: str | None = None, service=Depends(relations)):
    return await service.list(person_id, other_person_id, circle_id, kind, status,
                              include_incoming, include_archived, limit, cursor)


@router.patch("/relations/{relation_id}")
async def patch(relation_id: str, value: RelationPatch, service=Depends(relations)):
    return {"relation": await service.patch(relation_id, value)}


@router.delete("/relations/{relation_id}", status_code=204)
async def invalidate(relation_id: str, service=Depends(relations)):
    await service.invalidate(relation_id)
    return Response(status_code=204)


@router.get("/graph/shortest-path")
async def shortest_path(request: Request, source: str = Query(alias="from", min_length=1, max_length=128),
                        target: str = Query(alias="to", min_length=1, max_length=128),
                        max_depth: int = Query(default=4, ge=1, le=5)):
    return await request.app.state.paths.shortest_path(source, target, max_depth)
