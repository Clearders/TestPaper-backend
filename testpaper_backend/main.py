from __future__ import annotations

import uvicorn

from testpaper_backend.config import get_api_host, get_api_port, get_app_env, get_forwarded_allow_ips, validate_configuration
from testpaper_backend.core.logging_config import configure_library_loggers


def main() -> None:
    validate_configuration()
    configure_library_loggers()
    uvicorn.run(
        "testpaper_backend.application:app",
        host=get_api_host(),
        port=get_api_port(),
        reload=get_app_env() == "development",
        proxy_headers=True,
        forwarded_allow_ips=get_forwarded_allow_ips(),
    )
