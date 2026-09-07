"""FastAPI application factory and the `fastapi dev` / `uvicorn` entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ea.api.health import router as health_router
from ea.core.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Assemble the application.

    Taking `settings` as an argument keeps the app testable without touching
    the process environment.
    """
    settings = settings or get_settings()

    app = FastAPI(title=settings.app_name, debug=settings.debug)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    return app


app = create_app()
