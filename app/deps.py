from elasticsearch import AsyncElasticsearch
from fastapi import Depends, Request

from app.config import Settings, get_settings


def get_es(request: Request) -> AsyncElasticsearch:
    return request.app.state.es


def settings_dep() -> Settings:
    return get_settings()


ES = Depends(get_es)
SETTINGS = Depends(settings_dep)
