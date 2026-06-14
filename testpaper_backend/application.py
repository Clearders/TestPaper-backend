from __future__ import annotations

from fastapi.staticfiles import StaticFiles

from testpaper_backend.api.router import router
from testpaper_backend.core.factory import create_app
from testpaper_backend.core.http import register_exception_handlers, register_request_id_middleware, register_security_headers
from testpaper_backend.core.lifespan import lifespan
from testpaper_backend.services.images import IMAGE_UPLOAD_DIR
from testpaper_backend.services.profiles import AVATAR_UPLOAD_DIR

app = create_app(lifespan=lifespan)
IMAGE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount(
    "/api/v1/images/files",
    StaticFiles(directory=str(IMAGE_UPLOAD_DIR)),
    name="uploaded_images",
)
AVATAR_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount(
    "/api/v1/avatars",
    StaticFiles(directory=str(AVATAR_UPLOAD_DIR)),
    name="avatars",
)
register_request_id_middleware(app)
register_security_headers(app)
register_exception_handlers(app)
app.include_router(router)
