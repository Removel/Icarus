"""Application composition root. Database operations live in repositories."""
import asyncio
from contextlib import asynccontextmanager
import logging

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from api.circles import router as circle_router
from api.dependencies import authenticate
from api.persons import router as person_router
from api.profile import router as profile_router
from api.relations import router as relation_router
from config import Settings, load_settings
from domain.services.circle_service import CircleService
from domain.services.graph_service import GraphService
from domain.services.member_service import MemberService
from domain.services.person_service import PersonService
from domain.services.profile_service import ProfileService
from domain.services.relation_service import RelationService
from errors import ServiceError
from repositories.circle_repo import CircleRepository
from repositories.database import Database
from repositories.graph_client import GraphClient
from repositories.initialization import initialize_graph
from repositories.member_repo import MemberRepository
from repositories.person_repo import PersonRepository
from repositories.profile_repo import ProfileRepository
from repositories.relation_repo import RelationRepository

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, *, graph=None, database=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        config = settings or load_settings()
        app.state.settings = config
        graph_store = graph or GraphClient(config)
        profile_store = database or Database(config)
        app.state.graph = graph_store
        app.state.database = profile_store
        try:
            await initialize_graph(graph_store)
            await profile_store.initialize()
            people = PersonRepository(graph_store)
            await people.bootstrap(config.identity)
            app.state.persons = PersonService(people)
            app.state.profiles = ProfileService(ProfileRepository(profile_store), app.state.persons)
            app.state.circles = CircleService(CircleRepository(graph_store), app.state.persons)
            app.state.members = MemberService(MemberRepository(graph_store), app.state.persons, app.state.circles)
            relation_repository = RelationRepository(graph_store)
            app.state.relations = RelationService(relation_repository, app.state.persons, app.state.circles)
            app.state.paths = GraphService(relation_repository, app.state.persons)
            yield
        finally:
            await asyncio.gather(graph_store.close(), profile_store.close())

    app = FastAPI(title="rela-mem", version="0.1.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    for router in (person_router, profile_router, circle_router, relation_router):
        app.include_router(router)

    @app.exception_handler(ServiceError)
    async def service_error(request, error):
        headers = {"WWW-Authenticate": "Bearer"} if error.status == 401 else None
        return JSONResponse({"error": {"code": error.code, "message": error.message, "details": {}}},
                            status_code=error.status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        details = [{"field": ".".join(str(v) for v in item["loc"]), "type": item["type"]}
                   for item in error.errors()]
        return JSONResponse({"error": {"code": "VALIDATION_ERROR", "message": "invalid request parameters", "details": details}}, status_code=400)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, error):
        return JSONResponse({"error": {"code": "UNAVAILABLE", "message": "profile store unavailable", "details": {}}}, status_code=503)

    @app.exception_handler(Exception)
    async def unexpected_error(request, error):
        logger.error("unhandled request failure: %s", type(error).__name__)
        return JSONResponse({"error": {"code": "INTERNAL_ERROR", "message": "internal server error", "details": {}}}, status_code=500)

    @app.get("/openapi.json", dependencies=[Depends(authenticate)], include_in_schema=False)
    async def openapi_document():
        return app.openapi()

    @app.get("/healthz")
    async def health(request: Request):
        try:
            await asyncio.gather(request.app.state.graph.ping(), request.app.state.database.ping())
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return {"status": "ready"}

    return app


app = create_app()
