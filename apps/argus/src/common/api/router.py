"""Shared route composition; handlers do not trigger judgement or processing."""
from fastapi import APIRouter
from .health import create_health_router
from .observations import create_observation_router
from .wakes import create_wake_router


def create_router(*, database):
    router = APIRouter()
    router.include_router(create_health_router(database))
    router.include_router(create_observation_router())
    router.include_router(create_wake_router())
    return router
