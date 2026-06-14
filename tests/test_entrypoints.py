from __future__ import annotations

from testpaper_backend.application import app


def test_root_asgi_entrypoints_export_application() -> None:
    assert app is not None
    assert app.title == "TestPaper Backend"
