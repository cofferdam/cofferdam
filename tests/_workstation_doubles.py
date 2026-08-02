"""Shared fixtures for the workstation (M1) tests.

The workstation depends on FastAPI/uvicorn, which the Trust Core deliberately
does not. If those are missing, the workstation tests skip rather than fail, so
``python -m unittest discover`` still exercises the stdlib-only Trust Core suite
on a bare interpreter.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:  # pragma: no cover - import guard
    from fastapi.testclient import TestClient  # noqa: F401

    FASTAPI_AVAILABLE = True
except Exception:  # pragma: no cover
    FASTAPI_AVAILABLE = False

TEST_TOKEN = "test-token-do-not-use-in-production"


def require_fastapi() -> None:
    if not FASTAPI_AVAILABLE:
        raise unittest.SkipTest("fastapi/httpx not installed: pip install -e '.[workstation]'")


class WorkstationTestCase(unittest.TestCase):
    """Base case: isolated COFFERDAM_HOME, stub adapter, injected token."""

    adapter_failure = None

    def setUp(self) -> None:
        require_fastapi()
        from fastapi.testclient import TestClient

        from cofferdam.workstation.adapters.stub import StubAdapter
        from cofferdam.workstation.config import load_config
        from cofferdam.workstation.service import create_app

        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.config = load_config(home=self.home)
        self.config.ensure_dirs()
        self.adapter = StubAdapter(self.config, fail=self.adapter_failure)
        self.app = create_app(config=self.config, token=TEST_TOKEN, adapter=self.adapter)
        # Enter the client context so the app lifespan runs: it binds the event
        # loop that the action executor broadcasts onto. Without it, action
        # events are silently dropped and a test awaiting one blocks forever.
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self) -> None:
        client = getattr(self, "client", None)
        if client is not None:
            client.__exit__(None, None, None)
        tmp = getattr(self, "_tmp", None)
        if tmp is not None:
            tmp.cleanup()

    # -- helpers -------------------------------------------------------------

    @property
    def auth(self) -> dict:
        return {"Authorization": f"Bearer {TEST_TOKEN}"}

    def post_action(self, action: str, params: dict | None = None):
        return self.client.post(
            "/api/actions",
            json={"action": action, "params": params or {}},
            headers=self.auth,
        )
