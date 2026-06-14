from __future__ import annotations

from testpaper_backend.application import app


def test_root_asgi_entrypoints_export_application() -> None:
    assert app is not None
    assert app.title == "TestPaper Backend"


def test_openapi_marks_task_dispatch_as_post() -> None:
    operations = app.openapi()["paths"]["/api/v1/tasks/stats/questions"]
    assert "post" in operations
    assert "get" not in operations
