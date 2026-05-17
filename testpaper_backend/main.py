from __future__ import annotations

import uvicorn

from testpaper_backend.config import get_api_host, get_api_port


def main() -> None:
    uvicorn.run(
        "testpaper_backend.application:app",
        host=get_api_host(),
        port=get_api_port(),
        reload=True,
    )

