#!/usr/bin/env python3
"""Main entry point for Boxarr application."""

import asyncio
import signal
import sys
from pathlib import Path

import uvicorn

from src.utils.logger import setup_logging

# Setup logging first, before any other imports that might use logging
setup_logging()

from src.api.app import create_app_with_scheduler  # noqa: E402
from src.core.boxoffice import BoxOfficeService  # noqa: E402
from src.core.radarr import RadarrService  # noqa: E402
from src.core.scheduler import BoxarrScheduler  # noqa: E402
from src.utils.config import settings  # noqa: E402
from src.utils.logger import get_logger  # noqa: E402

logger = get_logger(__name__)


class BoxarrApplication:
    """Main application class."""

    def __init__(self):
        """Initialize application."""
        self.scheduler = None
        self.app = None
        self._shutdown_event = asyncio.Event()

    async def startup(self):
        """Application startup."""
        logger.info("Starting Boxarr application")

        # Check if configured
        if not settings.is_configured:
            logger.info("No configuration found - starting in setup mode")
            logger.info("Please visit http://localhost:8888 to configure Boxarr")
            # Don't exit - allow API to start so user can configure via web UI
        else:
            # Test Radarr connection only if configured
            try:
                with RadarrService() as radarr:
                    if not radarr.test_connection():
                        logger.warning(
                            "Failed to connect to Radarr - check configuration"
                        )
                    else:
                        logger.info(
                            f"Successfully connected to Radarr at {settings.radarr_url}"
                        )
            except Exception as e:
                logger.warning(f"Radarr connection failed: {e}")
                logger.info("Please check configuration via web UI")

        # Scheduler will be initialized in app creation
        if settings.is_configured and settings.boxarr_scheduler_enabled:
            logger.info("Scheduler will be started with the application")

        logger.info("Boxarr startup complete")

    async def shutdown(self):
        """Application shutdown."""
        logger.info("Shutting down Boxarr")

        # Scheduler cleanup is handled by FastAPI shutdown event
        self._shutdown_event.set()
        logger.info("Boxarr shutdown complete")

    def handle_signal(self, sig):
        """Handle shutdown signals."""
        logger.info(f"Received signal {sig}")
        asyncio.create_task(self.shutdown())

    async def run_api(self):
        """Run FastAPI application."""
        self.app = create_app_with_scheduler()

        config = uvicorn.Config(
            app=self.app,
            host=settings.boxarr_host,
            port=settings.boxarr_port,
            log_level=settings.log_level.lower(),
            access_log=True,
        )

        server = uvicorn.Server(config)

        # Start server in background
        asyncio.create_task(server.serve())

        # Wait for shutdown
        await self._shutdown_event.wait()

        # Stop server
        await server.shutdown()

    async def run_cli(self):
        """Run in CLI mode (no API)."""
        logger.info("Running in CLI mode")

        # Initialize scheduler for CLI mode
        self.scheduler = BoxarrScheduler(
            boxoffice_service=BoxOfficeService(),
            radarr_service=RadarrService() if settings.radarr_api_key else None,
        )

        # Run immediate update
        logger.info("Performing box office update...")

        try:
            results = await self.scheduler.update_box_office()

            print("\n" + "=" * 50)
            print("BOX OFFICE UPDATE RESULTS")
            print("=" * 50)
            print(f"Total movies: {results['total_count']}")
            print(f"Matched: {results['matched_count']}")
            print(f"Unmatched: {results['unmatched_count']}")

            if results["status_breakdown"]:
                print("\nStatus Breakdown:")
                for status, count in results["status_breakdown"].items():
                    print(f"  {status}: {count}")

            if results["matched_movies"]:
                print("\nMatched Movies:")
                for movie in results["matched_movies"]:
                    print(
                        f"  #{movie['rank']} {movie['title']} -> {movie['radarr_title']}"
                    )
                    print(
                        f"     Status: {movie['status']}, Has File: {movie['has_file']}"
                    )

            if results["unmatched_movies"]:
                print("\nUnmatched Movies:")
                for movie in results["unmatched_movies"]:
                    print(f"  #{movie['rank']} {movie['title']}")

            print("=" * 50 + "\n")

        except Exception as e:
            logger.error(f"Update failed: {e}")
            sys.exit(1)

    async def main(self, mode="api"):
        """
        Main application entry point.

        Args:
            mode: Run mode ("api" or "cli")
        """
        # CLI mode requires configuration
        if mode == "cli" and not settings.is_configured:
            logger.error(
                "CLI mode requires configuration. Please run in API mode first to configure."
            )
            sys.exit(1)

        # Setup signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig, lambda s=sig: self.handle_signal(s)  # type: ignore
            )

        try:
            await self.startup()

            if mode == "api":
                await self.run_api()
            else:
                await self.run_cli()

        except Exception as e:
            logger.error(f"Application error: {e}")
            raise
        finally:
            await self.shutdown()


def cli():
    """Command-line interface."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Boxarr - Box Office Tracking for Radarr"
    )
    parser.add_argument(
        "--mode",
        choices=["api", "cli", "update"],
        default="api",
        help="Run mode (default: api)",
    )
    parser.add_argument("--config", type=Path, help="Path to configuration file")
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="Override log level",
    )

    args = parser.parse_args()

    # Override settings if provided
    if args.config and args.config.exists():
        settings.load_from_yaml(args.config)

    if args.log_level:
        settings.log_level = args.log_level
        setup_logging()

    # Map update mode to cli
    mode = "cli" if args.mode == "update" else args.mode

    # Run application
    app = BoxarrApplication()

    try:
        asyncio.run(app.main(mode))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    cli()
