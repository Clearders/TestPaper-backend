from __future__ import annotations

from fastapi.staticfiles import StaticFiles

from testpaper_backend.api.router import router
from testpaper_backend.core.factory import create_app
from testpaper_backend.core.http import register_exception_handlers, register_request_id_middleware
from testpaper_backend.core.lifespan import lifespan
from testpaper_backend.services.images import IMAGE_UPLOAD_DIR

app = create_app(lifespan=lifespan)
app.mount(
    "/api/v1/images/files",
    StaticFiles(directory=str(IMAGE_UPLOAD_DIR)),
    name="uploaded_images",
)
register_request_id_middleware(app)
register_exception_handlers(app)
app.include_router(router)

