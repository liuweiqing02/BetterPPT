from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.files import router as files_router
from app.api.routes.health import router as health_router
from app.api.routes.metrics import router as metrics_router
from app.api.routes.tasks import router as tasks_router
from app.api.routes.templates import router as templates_router
from app.core.config import get_settings
from app.core.errors import AppException, app_exception_handler, unhandled_exception_handler
from app.db.base import Base
from app.db.session import engine
from app.models import *  # noqa: F403

settings = get_settings()

app = FastAPI(title=settings.app_name)
app.add_exception_handler(AppException, app_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

app.include_router(health_router, prefix=settings.api_prefix)
app.include_router(files_router, prefix=settings.api_prefix)
app.include_router(tasks_router, prefix=settings.api_prefix)
app.include_router(templates_router, prefix=settings.api_prefix)
app.include_router(metrics_router, prefix=settings.api_prefix)


@app.on_event('startup')
def on_startup() -> None:
    if settings.auto_create_tables:
        Base.metadata.create_all(bind=engine)

    for path in [settings.storage_root_path, settings.upload_root_path, settings.result_root_path]:
        Path(path).mkdir(parents=True, exist_ok=True)

