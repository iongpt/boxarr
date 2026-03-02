"""Boxarr API application."""

from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from .. import __version__
from ..core.radarr import RadarrService
from ..core.scheduler import BoxarrScheduler
from ..utils.config import settings
from ..utils.logger import get_logger
from .routes import (
    admin_router,
    boxoffice_router,
    config_router,
    movies_router,
    scheduler_router,
    web_router,
)

logger = get_logger(__name__)


def create_app(scheduler: Optional[BoxarrScheduler] = None) -> FastAPI:
    """
    Create and configure the FastAPI application.

    Args:
        scheduler: Optional scheduler instance for background tasks

    Returns:
        Configured FastAPI application
    """
    # Prepare root_path from settings
    base = settings.boxarr_url_base
    root_path = f"/{base}" if base else ""

    app = FastAPI(
        title="Boxarr",
        description="Box Office Tracking for Radarr - A local media management tool",
        version=__version__,
        root_path=root_path,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
    )

    # Add ProxyHeadersMiddleware to handle reverse proxy headers
    # Note: Remove trusted_hosts parameter for better security
    # Only add specific hosts if needed: trusted_hosts=["proxy.example.com"]
    app.add_middleware(ProxyHeadersMiddleware)

    # Add CORS middleware for local network access
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Allow all origins for local use
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount static files
    app.mount("/static", StaticFiles(directory="src/web/static"), name="static")

    # Include routers
    app.include_router(admin_router)
    app.include_router(config_router)
    app.include_router(boxoffice_router)
    app.include_router(movies_router)
    app.include_router(scheduler_router)
    app.include_router(web_router)

    # Store scheduler instance if provided
    if scheduler:
        app.state.scheduler = scheduler
        # Update scheduler reference in routes module
        from .routes import scheduler as scheduler_routes

        # Set the module-level variable correctly
        scheduler_routes._scheduler = scheduler

    @app.on_event("startup")
    async def startup_event():
        """Initialize application on startup."""
        logger.info("Boxarr API starting up...")

        # Start scheduler if configured and enabled
        if scheduler and settings.boxarr_scheduler_enabled:
            scheduler.start()
            logger.info("Scheduler started")

    @app.on_event("shutdown")
    async def shutdown_event():
        """Cleanup on application shutdown."""
        logger.info("Boxarr API shutting down...")

        # Stop scheduler if running
        if scheduler:
            scheduler.stop()
            logger.info("Scheduler stopped")

    @app.get("/api/health")
    async def health_check():
        """Simple health check endpoint."""
        radarr_connected = False
        if settings.radarr_api_key:
            try:
                with RadarrService() as r:
                    radarr_connected = r.test_connection()
            except Exception:
                radarr_connected = False

        return {
            "status": "healthy",
            "version": __version__,
            "radarr_configured": bool(settings.radarr_api_key),
            "radarr_connected": radarr_connected,
            "scheduler_enabled": settings.boxarr_scheduler_enabled,
        }

    return app


def create_app_with_scheduler() -> FastAPI:
    """Create application with scheduler enabled."""
    from ..core.boxoffice import BoxOfficeService
    from ..core.radarr import RadarrService

    # Initialize scheduler with services
    scheduler = BoxarrScheduler(
        boxoffice_service=BoxOfficeService(),
        radarr_service=RadarrService() if settings.radarr_api_key else None,
    )

    return create_app(scheduler)
