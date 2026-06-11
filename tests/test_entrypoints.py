from __future__ import annotations

import main as main_entrypoint
from testpaper_backend.application import app


def test_root_asgi_entrypoints_export_application() -> None:
    assert main_entrypoint.app is app
