from __future__ import annotations

import uvicorn

from testpaper_backend.config import get_api_host, get_api_port, get_app_env, get_forwarded_allow_ips


def main() -> None:
    uvicorn.run(
        "testpaper_backend.application:app",
        host=get_api_host(),
        port=get_api_port(),
        reload=get_app_env() == "development",
        proxy_headers=True,
        forwarded_allow_ips=get_forwarded_allow_ips(),
    )
