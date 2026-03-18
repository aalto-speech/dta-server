import asyncio

import pytest

from app.main import app


@pytest.fixture(scope="session", autouse=True)
def run_app_startup_shutdown() -> None:
    """Run FastAPI startup/shutdown events once for the test session."""
    asyncio.run(app.router.startup())
    yield
    asyncio.run(app.router.shutdown())
