from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

QUESTION_IMAGE_PATH_PREFIX = "/api/v1/images/files/"

_QUESTION_IMAGE_FILENAME_RE = re.compile(r"[0-9a-f]{32}\.png", re.IGNORECASE)


def normalize_question_image_url(url: str) -> str | None:
    """Return the canonical backend image path, or None when the URL is not allowed."""
    raw_url = url.strip()
    if not raw_url:
        return None

    parsed = urlsplit(raw_url)
    path = unquote(parsed.path or raw_url)
    if not path.startswith(QUESTION_IMAGE_PATH_PREFIX):
        return None

    filename = path[len(QUESTION_IMAGE_PATH_PREFIX) :]
    if "/" in filename or "\\" in filename:
        return None
    if not _QUESTION_IMAGE_FILENAME_RE.fullmatch(filename):
        return None

    return f"{QUESTION_IMAGE_PATH_PREFIX}{filename.lower()}"
