"""API route modules for Boxarr."""

from .boxoffice import router as boxoffice_router
from .config import router as config_router
from .movies import router as movies_router
from .scheduler import router as scheduler_router
from .web import router as web_router

__all__ = [
    "boxoffice_router",
    "config_router",
    "movies_router",
    "scheduler_router",
    "web_router",
]
